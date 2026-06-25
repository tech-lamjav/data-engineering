"""Sync de marts BigQuery -> Supabase Postgres via TRUNCATE + COPY.

Decisões de design (ver PLANO_OTIMIZACAO_BQ_SUPABASE.md fase 2):
- Usa `bq.list_rows()` (API tabledata.list, gratuita) em vez de `bq.query()` para
  evitar custo de scan recorrente.
- Usa `bq.get_table().schema` para parity check (API tables.get, gratuita) em vez
  de query em INFORMATION_SCHEMA.
- Sync serial table-by-table, dim -> fact -> derived (ver MART_TABLES_ORDERED em
  config.py) para minimizar janela de inconsistência cross-table.
- TRUNCATE + COPY dentro de uma única transação por tabela: leitores veem dados
  velhos ou novos, nunca parciais.
- COPY TIPADO do psycopg3 (`cur.copy(...).write_row(row)`): serializa tipos e NULL
  nativamente. Distingue None (NULL) de '' (string vazia real) — resolve o M11, em
  que o CSV textual com `NULL ''` colapsava ambos no mesmo token. Também faz
  streaming linha-a-linha (sem materializar a tabela inteira em StringIO).
"""
from typing import Iterable

import psycopg
from google.cloud import bigquery

from src.config import (
    BIGQUERY_DATASET,
    BIGQUERY_PROJECT_ID,
    MART_PG_SCHEMA,
    MART_TABLES_ORDERED,
    get_pg_url,
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


# ============================================================
# Normalização de tipos para schema parity
# ============================================================
# Canonicaliza BQ field_type e PG data_type em tokens comuns.
# Drift de tipo entre BQ e PG = COPY quebra silenciosamente.
_BQ_TYPE_TO_CANONICAL = {
    "INTEGER": "INT64",
    "INT64": "INT64",
    "FLOAT": "FLOAT64",
    "FLOAT64": "FLOAT64",
    "NUMERIC": "NUMERIC",
    "BIGNUMERIC": "NUMERIC",
    "BOOLEAN": "BOOL",
    "BOOL": "BOOL",
    "STRING": "TEXT",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "DATETIME": "TIMESTAMP",
    "TIME": "TIME",
}

_PG_TYPE_TO_CANONICAL = {
    "bigint": "INT64",
    "integer": "INT32",
    "smallint": "INT16",
    "double precision": "FLOAT64",
    "real": "FLOAT32",
    "numeric": "NUMERIC",
    "boolean": "BOOL",
    "text": "TEXT",
    "character varying": "TEXT",
    "varchar": "TEXT",
    "date": "DATE",
    "timestamp without time zone": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMP",
    "time without time zone": "TIME",
}


def _canon_bq(t: str) -> str:
    return _BQ_TYPE_TO_CANONICAL.get(t.upper(), t.upper())


def _canon_pg(t: str) -> str:
    return _PG_TYPE_TO_CANONICAL.get(t.lower(), t.lower())


# ============================================================
# Resolução de tabelas
# ============================================================
def resolve_tables(tables: str | Iterable[str] | None) -> list[str]:
    """Resolve seletor 'all' / lista de nomes para a ordem canônica do config."""
    if tables is None or tables == "all" or tables == ["all"]:
        return list(MART_TABLES_ORDERED)
    if isinstance(tables, str):
        requested = [t.strip() for t in tables.split(",") if t.strip()]
    else:
        requested = [t.strip() for t in tables if t and t.strip()]
    unknown = [t for t in requested if t not in MART_TABLES_ORDERED]
    if unknown:
        raise ValueError(
            f"Tabelas desconhecidas: {unknown}. "
            f"Tabelas válidas: {MART_TABLES_ORDERED}"
        )
    # Preserva ordem canônica (dim -> fact -> derivada)
    return [t for t in MART_TABLES_ORDERED if t in requested]


# ============================================================
# Schema parity check (pre-flight)
# ============================================================
def check_schema_parity(
    bq: bigquery.Client,
    pg_conn,
    tables: list[str],
) -> list[dict]:
    """Compara schema BQ vs Postgres por coluna. Retorna lista de drifts.

    Lista vazia = parity OK, seguro sincronizar.
    Cada drift: {table, kind, detail}, com kind in {missing_in_pg, missing_in_bq,
    type_mismatch}.
    """
    drifts: list[dict] = []

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = ANY(%s)
            """,
            (MART_PG_SCHEMA, tables),
        )
        pg_rows = cur.fetchall()

    pg_by_table: dict[str, dict[str, str]] = {}
    for table_name, column_name, data_type in pg_rows:
        pg_by_table.setdefault(table_name, {})[column_name] = data_type

    for table in tables:
        try:
            bq_table = bq.get_table(
                f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET}.{table}"
            )
        except Exception as e:
            drifts.append(
                {"table": table, "kind": "missing_in_bq", "detail": str(e)}
            )
            continue

        bq_cols = {f.name: f.field_type for f in bq_table.schema}
        pg_cols = pg_by_table.get(table)

        if pg_cols is None:
            drifts.append(
                {
                    "table": table,
                    "kind": "missing_in_pg",
                    "detail": "tabela não existe em nba_mart",
                }
            )
            continue

        for col, bq_type in bq_cols.items():
            if col not in pg_cols:
                drifts.append(
                    {
                        "table": table,
                        "kind": "missing_in_pg",
                        "detail": f"coluna '{col}' não existe em nba_mart.{table}",
                    }
                )
                continue
            canon_bq = _canon_bq(bq_type)
            canon_pg = _canon_pg(pg_cols[col])
            if canon_bq != canon_pg:
                drifts.append(
                    {
                        "table": table,
                        "kind": "type_mismatch",
                        "detail": (
                            f"coluna '{col}': BQ={bq_type} ({canon_bq}) vs "
                            f"PG={pg_cols[col]} ({canon_pg})"
                        ),
                    }
                )

        for col in pg_cols:
            if col not in bq_cols:
                drifts.append(
                    {
                        "table": table,
                        "kind": "missing_in_bq",
                        "detail": f"coluna '{col}' existe em PG mas não em BQ",
                    }
                )

    return drifts


# ============================================================
# Normalização de valor para COPY tipado (psycopg3)
# ============================================================
def _format_value(v):
    """Normaliza valor vindo do BQ para o COPY TIPADO do psycopg3.

    O COPY tipado (`copy.write_row`) serializa tipos Python e NULL nativamente,
    então a única responsabilidade aqui é repassar o valor preservando a
    distinção semântica entre None e string vazia (M11):

    - None  -> None  (vira NULL no Postgres).
    - ''    -> ''    (string vazia REAL, distinta de NULL).
    - demais tipos (bool, int, float, Decimal, date/datetime, str) são repassados
      como estão; o psycopg3 cuida da serialização para o tipo da coluna.

    Antes (M11): o CSV textual com `NULL ''` mapeava tanto None quanto '' para o
    mesmo token vazio, corrompendo strings vazias para NULL no destino.
    """
    return v


# ============================================================
# Sync state (skip-if-unchanged)
# ============================================================
# Cada Postgres tem seu próprio _sync_state (PRD e DEV são DBs independentes),
# por isso não precisa de coluna env. Tabela auto-criada na primeira execução
# após a Fase 3 migration (que cria o schema nba_mart).
_SYNC_STATE_TABLE = f'"{MART_PG_SCHEMA}"."_sync_state"'


def _ensure_sync_state_table(pg_conn) -> None:
    """CREATE TABLE IF NOT EXISTS pro state. Idempotente."""
    with pg_conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_SYNC_STATE_TABLE} (
                table_name text PRIMARY KEY,
                last_synced_bq_modified_time timestamptz NOT NULL,
                last_synced_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    pg_conn.commit()


def _read_last_synced(pg_conn, table_name: str):
    """Retorna last_synced_bq_modified_time ou None se nunca sincronizado."""
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT last_synced_bq_modified_time FROM {_SYNC_STATE_TABLE} "
            f"WHERE table_name = %s",
            (table_name,),
        )
        row = cur.fetchone()
    return row[0] if row else None


# ============================================================
# Sync de uma tabela
# ============================================================
def _sync_one_table(
    bq: bigquery.Client,
    pg_conn,
    table_name: str,
    force: bool = False,
) -> dict:
    """Sincroniza uma mart: TRUNCATE + COPY dentro de uma única transação.

    Skip-if-unchanged: se bq.get_table().modified <= last_synced_bq_modified_time
    em _sync_state, pula essa tabela (sem TRUNCATE, sem locking).

    force=True (modo full-resync) ignora o skip-if-unchanged e re-sincroniza a
    tabela mesmo que o BQ não tenha mudado — útil quando o Postgres sofreu drift
    fora do sync (ex.: truncate manual) e o state ficaria pulando indefinidamente.
    """
    # Invariante de segurança: table_name vem SEMPRE da allowlist MART_TABLES_ORDERED
    # (via resolve_tables). O assert torna explícita a segurança das f-strings de SQL.
    assert table_name in MART_TABLES_ORDERED, f"tabela fora da allowlist: {table_name!r}"
    table_ref = f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET}.{table_name}"

    # list_rows usa tabledata.list (grátis), não cria query job. O .table devolvido
    # já carrega o schema, evitando uma segunda chamada bq.get_table só pelo modified.
    rows_iter = bq.list_rows(table_ref)
    bq_modified = rows_iter.table.modified  # timezone-aware datetime

    last_synced = _read_last_synced(pg_conn, table_name)
    if not force and last_synced is not None and bq_modified <= last_synced:
        logger.info(
            f"Skip {table_name}: BQ não mudou (modified={bq_modified.isoformat()}, "
            f"last_synced={last_synced.isoformat()})"
        )
        return {"table": table_name, "rows": 0, "skipped": True}

    logger.info(
        f"Sincronizando {table_name} (BQ modified={bq_modified.isoformat()}"
        f"{', force=True' if force else ''})"
    )

    columns = [field.name for field in rows_iter.schema]
    column_list = ", ".join(f'"{c}"' for c in columns)

    with pg_conn.cursor() as cur:
        # BEGIN é implícito quando autocommit=False; TRUNCATE + COPY + state-update
        # ficam numa única transação. Se qualquer passo falhar, rollback total
        # mantém o state consistente com o dado.
        cur.execute(f'TRUNCATE TABLE "{MART_PG_SCHEMA}"."{table_name}"')
        # COPY TIPADO: write_row recebe a tupla nativa (None vira NULL, '' fica '').
        # Streaming linha-a-linha — não materializa a tabela inteira em memória.
        row_count = 0
        with cur.copy(
            f'COPY "{MART_PG_SCHEMA}"."{table_name}" ({column_list}) FROM STDIN'
        ) as copy:
            for row in rows_iter:
                copy.write_row([_format_value(row[c]) for c in columns])
                row_count += 1
        cur.execute(
            f"""
            INSERT INTO {_SYNC_STATE_TABLE}
                (table_name, last_synced_bq_modified_time, last_synced_at)
            VALUES (%s, %s, now())
            ON CONFLICT (table_name) DO UPDATE
                SET last_synced_bq_modified_time = EXCLUDED.last_synced_bq_modified_time,
                    last_synced_at = EXCLUDED.last_synced_at
            """,
            (table_name, bq_modified),
        )
    pg_conn.commit()

    logger.info(f"OK {table_name}: {row_count} linhas")
    return {"table": table_name, "rows": row_count, "skipped": False}


# ============================================================
# Orquestrador
# ============================================================
def run_sync(
    tables: str | Iterable[str] | None = None,
    env: str = "prd",
    force: bool = False,
) -> dict:
    """Executa o sync. Roda pre-flight de schema parity antes de qualquer TRUNCATE.

    Args:
        tables: 'all' (default), CSV string, ou lista. Filtragem preserva
                ordem canônica (dim -> fact -> derivada).
        env: 'prd' (default) ou 'dev'. Determina qual SUPABASE_PG_URL_* usar.
        force: True ignora o skip-if-unchanged e força full-resync de todas as
               tabelas resolvidas (recupera de drift no Postgres feito fora do sync).

    Returns:
        {status, env, synced: [...], drift: [...]}
        Em caso de drift detectada no pre-flight, NÃO faz TRUNCATE em nenhuma
        tabela; retorna status='aborted_schema_drift' com o detalhe.
    """
    pg_url = get_pg_url(env)
    resolved = resolve_tables(tables)
    logger.info(
        f"Sync solicitado env={env} para {len(resolved)} tabela(s): {resolved}"
        f"{' (force/full-resync)' if force else ''}"
    )

    bq = bigquery.Client(project=BIGQUERY_PROJECT_ID)
    pg_conn = psycopg.connect(pg_url)
    pg_conn.autocommit = False

    try:
        drifts = check_schema_parity(bq, pg_conn, resolved)
        if drifts:
            logger.error(
                f"Schema drift detectado (env={env}), abortando sync ANTES de "
                f"qualquer TRUNCATE. Drifts: {drifts}"
            )
            return {
                "status": "aborted_schema_drift",
                "env": env,
                "drift": drifts,
                "synced": [],
            }

        _ensure_sync_state_table(pg_conn)

        synced: list[dict] = []
        for table in resolved:
            result = _sync_one_table(bq, pg_conn, table, force=force)
            synced.append(result)

        n_synced = sum(1 for r in synced if not r.get("skipped"))
        n_skipped = sum(1 for r in synced if r.get("skipped"))
        logger.info(
            f"Sync env={env} concluído: {n_synced} tabela(s) sincronizada(s), "
            f"{n_skipped} pulada(s) por BQ inalterado"
        )

        return {
            "status": "success",
            "env": env,
            "synced": synced,
            "drift": [],
            "summary": {"synced": n_synced, "skipped": n_skipped},
        }

    except Exception:
        pg_conn.rollback()
        raise
    finally:
        pg_conn.close()
