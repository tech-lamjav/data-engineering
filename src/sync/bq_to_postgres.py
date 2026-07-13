"""Sync de marts BigQuery -> Supabase Postgres via TRUNCATE + COPY.

Decisões de design (ver PLANO_OTIMIZACAO_BQ_SUPABASE.md fase 2):
- Usa `bq.list_rows()` (API tabledata.list, gratuita) em vez de `bq.query()` para
  evitar custo de scan recorrente.
- Usa `bq.get_table().schema` para parity check (API tables.get, gratuita) em vez
  de query em INFORMATION_SCHEMA.
- Sync serial table-by-table, dim -> fact -> derived (ver *_TABLES_ORDERED em
  config.py) para minimizar janela de inconsistência cross-table.
- TRUNCATE + COPY dentro de uma única transação por tabela: leitores veem dados
  velhos ou novos, nunca parciais.
- COPY TIPADO do psycopg3 (`cur.copy(...).write_row(row)`): serializa tipos e NULL
  nativamente. Distingue None (NULL) de '' (string vazia real) — resolve o M11, em
  que o CSV textual com `NULL ''` colapsava ambos no mesmo token. Também faz
  streaming linha-a-linha (sem materializar a tabela inteira em StringIO).
- Sessão Postgres com `SET statement_timeout = '900s'`: o default do Supabase no
  nível do database é 2min, insuficiente p/ COPY de marts grandes em compute
  pequeno (DEV cancelou fact_fixture_player_stats em 10/07 com QueryCanceled).
  E `connect_timeout=15`: fail-fast quando o pooler não completa o handshake
  (incidente Supavisor 05-06/07 prendia o connect ~381s); retry fica no workflow.

Multi-esporte: o engine é sport-agnostic. `run_sync(sport=...)` resolve via
`config.get_sync_target()` o trio (dataset BQ, schema Postgres, allowlist ordenada).
'nba' -> dataset `nba` / schema `nba_mart`; 'futebol' -> dataset `futebol` /
schema `futebol`. Colunas BQ complexas (REPEATED/RECORD) são puladas: o Postgres
nativo é escalar (no futebol, dim_leagues.coverage é RECORD e os arrays de
evidências/avisos são reconstruídos nas RPCs a partir de colunas boolean).
"""
from typing import Iterable

import psycopg
from google.cloud import bigquery

from src.config import (
    BIGQUERY_PROJECT_ID,
    get_pg_url,
    get_sync_target,
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


def _is_complex_field(field) -> bool:
    """True se o campo BQ é REPEATED (array) ou RECORD/STRUCT.

    Esses campos NÃO vão para o Postgres nativo (que é escalar): são pulados tanto
    no parity check quanto no COPY. Ex.: futebol `dim_leagues.coverage` (RECORD) e
    `fact_value_opportunities.evidencias`/`.avisos` (ARRAY<STRING>) — no app as
    evidências/avisos são reconstruídas nas RPCs a partir de colunas boolean.
    O caminho NBA não tem colunas complexas, então o comportamento dele é inalterado.
    """
    return field.mode == "REPEATED" or field.field_type in ("RECORD", "STRUCT")


# ============================================================
# Resolução de tabelas
# ============================================================
def resolve_tables(
    tables: str | Iterable[str] | None,
    tables_ordered: list[str],
) -> list[str]:
    """Resolve seletor 'all' / lista de nomes para a ordem canônica do esporte."""
    if tables is None or tables == "all" or tables == ["all"]:
        return list(tables_ordered)
    if isinstance(tables, str):
        requested = [t.strip() for t in tables.split(",") if t.strip()]
    else:
        requested = [t.strip() for t in tables if t and t.strip()]
    unknown = [t for t in requested if t not in tables_ordered]
    if unknown:
        raise ValueError(
            f"Tabelas desconhecidas: {unknown}. "
            f"Tabelas válidas: {tables_ordered}"
        )
    # Preserva ordem canônica (dim -> fact -> derivada)
    return [t for t in tables_ordered if t in requested]


# ============================================================
# Schema parity check (pre-flight)
# ============================================================
def check_schema_parity(
    bq: bigquery.Client,
    pg_conn,
    tables: list[str],
    dataset: str,
    schema: str,
) -> list[dict]:
    """Compara schema BQ vs Postgres por coluna. Retorna lista de drifts.

    Lista vazia = parity OK, seguro sincronizar.
    Cada drift: {table, kind, detail}, com kind in {missing_in_pg, missing_in_bq,
    type_mismatch}. Colunas BQ complexas (REPEATED/RECORD) são ignoradas — não são
    esperadas no Postgres escalar.
    """
    drifts: list[dict] = []

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = ANY(%s)
            """,
            (schema, tables),
        )
        pg_rows = cur.fetchall()

    pg_by_table: dict[str, dict[str, str]] = {}
    for table_name, column_name, data_type in pg_rows:
        pg_by_table.setdefault(table_name, {})[column_name] = data_type

    for table in tables:
        try:
            bq_table = bq.get_table(
                f"{BIGQUERY_PROJECT_ID}.{dataset}.{table}"
            )
        except Exception as e:
            drifts.append(
                {"table": table, "kind": "missing_in_bq", "detail": str(e)}
            )
            continue

        # Só colunas escalares contam para parity (complexas são puladas no COPY).
        bq_cols = {
            f.name: f.field_type
            for f in bq_table.schema
            if not _is_complex_field(f)
        }
        pg_cols = pg_by_table.get(table)

        if pg_cols is None:
            drifts.append(
                {
                    "table": table,
                    "kind": "missing_in_pg",
                    "detail": f"tabela não existe em {schema}",
                }
            )
            continue

        for col, bq_type in bq_cols.items():
            if col not in pg_cols:
                drifts.append(
                    {
                        "table": table,
                        "kind": "missing_in_pg",
                        "detail": f"coluna '{col}' não existe em {schema}.{table}",
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
                        "detail": (
                            f"coluna '{col}' existe em PG mas não em BQ "
                            f"(ou é coluna complexa REPEATED/RECORD, pulada)"
                        ),
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
# após a migration que cria o schema destino. É por-schema (nba_mart._sync_state,
# futebol._sync_state) para não misturar o estado dos esportes.
def _sync_state_table(schema: str) -> str:
    return f'"{schema}"."_sync_state"'


def _ensure_sync_state_table(pg_conn, schema: str) -> None:
    """CREATE TABLE IF NOT EXISTS pro state. Idempotente."""
    with pg_conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_sync_state_table(schema)} (
                table_name text PRIMARY KEY,
                last_synced_bq_modified_time timestamptz NOT NULL,
                last_synced_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
    pg_conn.commit()


def _read_last_synced(pg_conn, table_name: str, schema: str):
    """Retorna last_synced_bq_modified_time ou None se nunca sincronizado."""
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT last_synced_bq_modified_time FROM {_sync_state_table(schema)} "
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
    dataset: str,
    schema: str,
    tables_ordered: list[str],
    force: bool = False,
) -> dict:
    """Sincroniza uma mart: TRUNCATE + COPY dentro de uma única transação.

    Skip-if-unchanged: se bq.get_table().modified <= last_synced_bq_modified_time
    em _sync_state, pula essa tabela (sem TRUNCATE, sem locking).

    force=True (modo full-resync) ignora o skip-if-unchanged e re-sincroniza a
    tabela mesmo que o BQ não tenha mudado — útil quando o Postgres sofreu drift
    fora do sync (ex.: truncate manual) e o state ficaria pulando indefinidamente.

    Colunas BQ complexas (REPEATED/RECORD) são puladas — o Postgres nativo é
    escalar. O column_list do COPY usa só as escalares, casando com o DDL nativo.
    """
    # Invariante de segurança: table_name vem SEMPRE da allowlist resolvida
    # (via resolve_tables). O assert torna explícita a segurança das f-strings de SQL.
    assert table_name in tables_ordered, f"tabela fora da allowlist: {table_name!r}"
    table_ref = f"{BIGQUERY_PROJECT_ID}.{dataset}.{table_name}"

    # list_rows usa tabledata.list (grátis), não cria query job. Em versões antigas
    # do client (<=3.27) o RowIterator expõe `.table` (reaproveita o schema e evita
    # um get_table); em 3.40+ esse atributo sumiu. Fallback robusto p/ get_table
    # (API tables.get, também grátis). NBA mantém o caminho rápido onde `.table` existe.
    rows_iter = bq.list_rows(table_ref)
    _iter_table = getattr(rows_iter, "table", None)
    bq_modified = (
        _iter_table.modified
        if _iter_table is not None
        else bq.get_table(table_ref).modified
    )  # timezone-aware datetime

    last_synced = _read_last_synced(pg_conn, table_name, schema)
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

    # Pula colunas complexas (REPEATED/RECORD); só escalares vão pro COPY.
    all_fields = list(rows_iter.schema)
    columns = [f.name for f in all_fields if not _is_complex_field(f)]
    skipped_cols = [f.name for f in all_fields if _is_complex_field(f)]
    if skipped_cols:
        logger.info(
            f"{table_name}: {len(skipped_cols)} coluna(s) complexa(s) pulada(s) "
            f"(REPEATED/RECORD): {skipped_cols}"
        )
    column_list = ", ".join(f'"{c}"' for c in columns)

    with pg_conn.cursor() as cur:
        # BEGIN é implícito quando autocommit=False; TRUNCATE + COPY + state-update
        # ficam numa única transação. Se qualquer passo falhar, rollback total
        # mantém o state consistente com o dado.
        cur.execute(f'TRUNCATE TABLE "{schema}"."{table_name}"')
        # COPY TIPADO: write_row recebe a tupla nativa (None vira NULL, '' fica '').
        # Streaming linha-a-linha — não materializa a tabela inteira em memória.
        row_count = 0
        with cur.copy(
            f'COPY "{schema}"."{table_name}" ({column_list}) FROM STDIN'
        ) as copy:
            for row in rows_iter:
                copy.write_row([_format_value(row[c]) for c in columns])
                row_count += 1
        cur.execute(
            f"""
            INSERT INTO {_sync_state_table(schema)}
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
    sport: str = "nba",
) -> dict:
    """Executa o sync. Roda pre-flight de schema parity antes de qualquer TRUNCATE.

    Args:
        tables: 'all' (default), CSV string, ou lista. Filtragem preserva
                ordem canônica (dim -> fact -> derivada).
        env: 'prd' (default) ou 'dev'. Determina qual SUPABASE_PG_URL_* usar.
        force: True ignora o skip-if-unchanged e força full-resync de todas as
               tabelas resolvidas (recupera de drift no Postgres feito fora do sync).
        sport: 'nba' (default) ou 'futebol'. Resolve dataset BQ + schema Postgres +
               allowlist via config.get_sync_target().

    Returns:
        {status, sport, env, synced: [...], drift: [...]}
        Em caso de drift detectada no pre-flight, NÃO faz TRUNCATE em nenhuma
        tabela; retorna status='aborted_schema_drift' com o detalhe.
    """
    dataset, schema, tables_ordered = get_sync_target(sport)
    pg_url = get_pg_url(env)
    resolved = resolve_tables(tables, tables_ordered)
    logger.info(
        f"Sync solicitado sport={sport} env={env} para {len(resolved)} "
        f"tabela(s) [{dataset} -> {schema}]: {resolved}"
        f"{' (force/full-resync)' if force else ''}"
    )

    bq = bigquery.Client(project=BIGQUERY_PROJECT_ID)
    # connect_timeout é por host tentado (o DNS do pooler tem múltiplos A records);
    # sem ele, pooler degradado = connect preso por minutos em vez de falhar rápido.
    pg_conn = psycopg.connect(pg_url, connect_timeout=15)
    pg_conn.autocommit = False

    try:
        # Override por sessão do statement_timeout=2min que o Supabase seta no
        # database: COPY de marts grandes excede 2min (sobretudo no compute menor
        # do DEV). 900s alinha com o timeout do Cloud Run; não altera nada global.
        with pg_conn.cursor() as cur:
            cur.execute("SET statement_timeout = '900s'")
        pg_conn.commit()

        drifts = check_schema_parity(bq, pg_conn, resolved, dataset, schema)
        if drifts:
            logger.error(
                f"Schema drift detectado (sport={sport}, env={env}), abortando sync "
                f"ANTES de qualquer TRUNCATE. Drifts: {drifts}"
            )
            return {
                "status": "aborted_schema_drift",
                "sport": sport,
                "env": env,
                "drift": drifts,
                "synced": [],
            }

        _ensure_sync_state_table(pg_conn, schema)

        synced: list[dict] = []
        for table in resolved:
            result = _sync_one_table(
                bq, pg_conn, table, dataset, schema, tables_ordered, force=force
            )
            synced.append(result)

        n_synced = sum(1 for r in synced if not r.get("skipped"))
        n_skipped = sum(1 for r in synced if r.get("skipped"))
        logger.info(
            f"Sync sport={sport} env={env} concluído: {n_synced} tabela(s) "
            f"sincronizada(s), {n_skipped} pulada(s) por BQ inalterado"
        )

        return {
            "status": "success",
            "sport": sport,
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
