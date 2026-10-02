"""Sync de marts BigQuery -> Supabase Postgres via TRUNCATE + COPY.

Decisões de design (ver PLANO_OTIMIZACAO_BQ_SUPABASE.md fase 2):
- Usa `bq.list_rows()` (API tabledata.list, gratuita) em vez de `bq.query()` para
  evitar custo de scan recorrente. ÚNICAS exceções: (DE#106) em DEV, a tabela com regra de
  retenção é lida por query job filtrado (`filtro_bq`), para o corte rodar no BigQuery em vez
  de em Python depois de ler tudo; (DE#109) em PRD, `fact_odds_snapshot` com o cache de serving
  ligado pelo workflow (`odds_serving`: mercados servidos, fixtures dos últimos 30 dias e
  futuras com todas as janelas, o resto só com o fechamento T-15m). Isso exige
  `bigquery.jobs.create` na SA de runtime do sync (conta dedicada, não a dos 29 serviços).
- Usa `bq.get_table().schema` para parity check (API tables.get, gratuita) em vez
  de query em INFORMATION_SCHEMA.
- Sync serial table-by-table, dim -> fact -> derived (ver *_TABLES_ORDERED em
  config.py) para minimizar janela de inconsistência cross-table.
- Duas formas de carga, escolhidas POR TABELA no início da carga dela (DE#108, ADR 0005):
  * CARGA POR TROCA (`src/sync/troca.py`, habilitada por tabela pelo parâmetro `troca` do
    serviço; desligada por padrão): o COPY vai para uma tabela-sombra, fora do caminho dos
    leitores, que seguem vendo o dado ANTIGO até a troca por RENAME, de milissegundos. O único
    ponto de espera é o RENAME (teto de 2 s, com retentativas); se não trocar, a tabela falha
    alto e a vigente fica intacta. Tabela com dependente (view etc.) não usa a troca: vai para
    o caminho STAGED (parâmetro `staged`) ou para a carga no lugar, com WARNING.
  * CARGA NO LUGAR (o default; NBA e DEV fora do canário): TRUNCATE + COPY na mesma transação.
    Nenhum leitor vê dado parcial, MAS o TRUNCATE segura ACCESS EXCLUSIVE até o fim do COPY:
    o leitor NÃO vê o dado antigo, ele ESPERA e o PostgREST o cancela por timeout (o docstring
    antigo afirmava o contrário; isso era falso). É por isso que a troca existe.
- Trava por (sport, env): `pg_try_advisory_lock` de sessão logo depois do connect (DE#107,
  src/sync/trava.py). Lock ocupado = o sync volta com STATUS_OCUPADO sem tocar em nada.
- COPY TIPADO do psycopg3 (`cur.copy(...).write_row(row)`): serializa tipos e NULL
  nativamente. Distingue None (NULL) de '' (string vazia real) — resolve o M11, em
  que o CSV textual com `NULL ''` colapsava ambos no mesmo token. Também faz
  streaming linha-a-linha (sem materializar a tabela inteira em StringIO).
- Sessão Postgres com `SET statement_timeout` de SYNC_STATEMENT_TIMEOUT_S (3600 s, o
  timeout do Cloud Run): o default do Supabase no nível do database é 2min,
  insuficiente p/ COPY de marts grandes em compute pequeno (DEV cancelou
  fact_fixture_player_stats em 10/07 com QueryCanceled; a DE#107 subiu de 900 s para
  3600 s porque o COPY de odds já passou de 860 s).
  E `connect_timeout=15`: fail-fast quando o pooler não completa o handshake
  (incidente Supavisor 05-06/07 prendia o connect ~381s); retry fica no workflow.

Multi-esporte: o engine é sport-agnostic. `run_sync(sport=...)` resolve via
`alvo.resolve_alvo_sync()` o trio (dataset BQ, schema Postgres, allowlist ordenada menos
as exclusões — ex.: `int_futebol_odds_devig` saiu do sync, DE#112).
'nba' -> dataset `nba` / schema `nba_mart`; 'futebol' -> dataset `futebol` /
schema `futebol`. Colunas BQ complexas (REPEATED/RECORD) são puladas: o Postgres
nativo é escalar (no futebol, dim_leagues.coverage é RECORD e os arrays de
evidências/avisos são reconstruídos nas RPCs a partir de colunas boolean).
"""
import select
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

import psycopg
from google.cloud import bigquery

from src.config import (
    BIGQUERY_PROJECT_ID,
    get_pg_url,
)
from src.sync.alvo import resolve_alvo_sync
from src.sync.filtro_bq import FiltroBQ, le_tabela_filtrada
from src.sync import odds_serving
from src.sync import troca as troca_mod
from src.sync.retencao import resolve_regra_retencao
from src.sync.tamanho_dev import medir_tamanho_dev_mb
from src.sync.trava import (
    STATUS_OCUPADO,
    SYNC_STATEMENT_TIMEOUT_S,
    chave_trava,
    solta_trava,
    tenta_trava,
    verifica_pooler_de_sessao,
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
# Backpressure do COPY (DE#110)
# ============================================================
# No Linux o psycopg NÃO faz flush no COPY (`PREFER_FLUSH` é só macOS): `write_row` apenas
# enfileira no buffer de saída da libpq, que em modo não bloqueante cresce sem limite.
# Com o servidor ingerindo mais devagar que o BQ entrega, o RSS sobe até o tamanho do
# backlog (~220 B/linha medido; fact_odds_snapshot inteira seria ≤ ~900 MiB, extrapolação
# linear) e, como a libpq não encolhe o buffer, o pico fica preso ao processo. Flush
# periódico com espera no socket prende o produtor ao ritmo do servidor e limita o buffer
# a ~COPY_FLUSH_EVERY_ROWS linhas (medido: 251 -> 74 MiB, 1 mi linhas a 5 MB/s, sem custo
# mensurável de tempo). Limites conhecidos: o teto é por LINHAS, não por bytes (tabelas
# largas pedem N menor); `select.select` só aceita fd < 1024 (o fd do PG é alocado uma
# vez no connect, baixo); e isto NÃO toca a memória por página do `list_rows` do BQ.
COPY_FLUSH_EVERY_ROWS = 5_000


def _flush_copy_buffer(pg_conn) -> None:
    """Esvazia o buffer de saída da libpq, bloqueando enquanto o servidor não acompanhar.

    Segue o contrato do PQflush em modo não bloqueante: 1 = pendente; esperar o socket
    ficar gravável OU legível (se legível, consumir o input antes de tentar de novo).
    Sem timeout, como as esperas do próprio psycopg: o teto é o timeout do Cloud Run.
    """
    pgconn = pg_conn.pgconn
    while pgconn.flush() != 0:
        readable, _, _ = select.select([pgconn.socket], [pgconn.socket], [], None)
        if readable:
            pgconn.consume_input()


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
# Retenção de DEV (DE#75/#76/#77; filtro no BigQuery desde a DE#106)
# ============================================================
# A regra é resolvida por (esporte, ambiente, tabela) em `src.sync.retencao` (e NÃO em
# `src.config`, que deriva os 29 serviços). Desde a DE#106 o corte roda NO BIGQUERY: a tabela
# com regra, em DEV, é lida por query job parametrizado (`src.sync.filtro_bq`) e só as linhas
# que entram chegam ao processo; antes, `list_rows` trazia a tabela inteira e um predicado em
# Python descartava (4,16 mi de linhas lidas para gravar 698 mil, em odds).
# PRD e tabela sem regra: rule é None, nenhuma query job, `list_rows` byte-idêntico ao anterior.
# O que decide continua sendo a ordem: parity check e skip-if-unchanged vêm ANTES do job.
_TETO_BYTES_FATURADOS_MINIMO = 100 * 1024 * 1024
_FATOR_TETO_BYTES_FATURADOS = 2


def _teto_de_bytes_faturados(num_bytes) -> int:
    """Teto do query job, proporcional ao tamanho lógico da tabela, com folga.

    Uma query nunca fatura mais que a tabela inteira (só lê colunas dela); o fator 2 é folga
    para o tamanho mudar entre a leitura dos metadados e o job, e o piso evita teto menor que o
    mínimo de cobrança do BigQuery em tabela minúscula. Estouro = o BigQuery recusa sem cobrar.
    """
    return max(int((num_bytes or 0) * _FATOR_TETO_BYTES_FATURADOS), _TETO_BYTES_FATURADOS_MINIMO)


def _tipo_da_coluna(campos, nome: str) -> str:
    """Tipo BQ da coluna no schema da tabela. Coluna ausente falha ANTES do job."""
    for campo in campos:
        if campo.name == nome:
            return campo.field_type.upper()
    raise ValueError(
        f"coluna '{nome}' da regra de retenção não existe no schema BigQuery da tabela"
    )


def _filtro_da_regra(rule: dict, campos, agora: datetime, eligible_fixture_ids) -> FiltroBQ:
    """Traduz a regra de retenção no filtro do BigQuery. NULL na coluna da regra nunca passa
    (em SQL, NULL >= x, NULL = x e NULL IN (...) não são verdadeiros): ausência de data ou
    temporada não é retenção, é dado sem como avaliar o corte.

    - timestamp_days: coluna TIMESTAMP ou DATE >= agora - `days`. BQ nunca usa fuso local, então
      o corte sai de UTC. Com `partition_column`, soma um corte de partição com 1 dia de folga
      (uma coluna de partição derivada em outro fuso não pode perder linha que o corte por
      instante deixaria entrar) para o job não ler dias inteiros que a retenção descarta.
    - season: coluna == temporada corrente configurada.
    - fixture_window: coluna IN (fixtures elegíveis, lidas do Postgres de destino).
    - cache_serving (PRD, DE#109): mercados servidos E (fixture elegível, lida do BigQuery, OU a
      janela de fechamento). Ver `odds_serving`.

    Uma regra com `market_ids` (as odds, em DEV e em PRD) soma `market_id IN mercados` ao filtro.
    """
    kind = rule["kind"]
    coluna = rule["column"]
    tipo = _tipo_da_coluna(campos, coluna)
    filtro = _filtro_base_da_regra(rule, kind, coluna, tipo, campos, agora, eligible_fixture_ids)
    if rule.get("market_ids") is not None and kind != "cache_serving":
        # O cache de serving já compõe os mercados dentro do próprio filtro.
        _tipo_da_coluna(campos, rule["market_column"])
        filtro = filtro.e(odds_serving.filtro_mercados(rule))
    return filtro


def _filtro_base_da_regra(rule, kind, coluna, tipo, campos, agora, eligible_fixture_ids) -> FiltroBQ:
    if kind == "timestamp_days":
        corte = agora - timedelta(days=rule["days"])
        if tipo == "DATE":
            valor = corte.date()
        elif tipo == "TIMESTAMP":
            valor = corte
        else:
            raise ValueError(
                f"retenção por tempo em coluna '{coluna}' do tipo {tipo}: só TIMESTAMP e DATE"
            )
        filtro = FiltroBQ.desde(coluna, valor, "corte")
        particao = rule.get("partition_column")
        if particao:
            _tipo_da_coluna(campos, particao)
            filtro = filtro.e(
                FiltroBQ.desde(particao, corte.date() - timedelta(days=1), "corte_particao")
            )
        return filtro
    if kind == "season":
        return FiltroBQ.igual(coluna, rule["season"], "temporada")
    if kind == "fixture_window":
        return FiltroBQ.em_lista(coluna, eligible_fixture_ids or (), "ids")
    if kind == "cache_serving":
        _tipo_da_coluna(campos, rule["market_column"])
        _tipo_da_coluna(campos, rule["closing_column"])
        return odds_serving.filtro_cache_serving(rule, eligible_fixture_ids or ())
    raise ValueError(f"kind de regra de retenção desconhecido: {kind!r}")


def _verifica_query_job(bq: bigquery.Client) -> None:
    """Pré-voo de IAM: prova `bigquery.jobs.create` com um dry-run, ANTES de qualquer TRUNCATE.

    A retenção de DEV lê por query job, que exige essa permissão na conta de runtime (o
    `list_rows` não exigia, e `extractscripts@` não a tem). Sem o pré-voo, a falta dela só
    apareceria na primeira tabela com regra, depois de as anteriores já terem sido esvaziadas e
    recarregadas, e o workflow ainda repetiria a chamada em 5xx. Aqui o 403 aborta o sync INTEIRO
    com a mesma forma do aborto do parity check (nada tocado). Dry-run também exige a permissão e
    não custa nada.
    """
    try:
        bq.query("SELECT 1", job_config=bigquery.QueryJobConfig(dry_run=True))
    except Exception as e:
        logger.error(
            f"Sync abortado ANTES de qualquer TRUNCATE: a conta de runtime não consegue criar "
            f"query jobs no BigQuery ({type(e).__name__}: {e}). A retenção de DEV (DE#106) e o "
            f"cache de serving de PRD (DE#109) leem por query job e exigem "
            f"`bigquery.jobs.create` (ex.: roles/bigquery.jobUser) na conta de runtime do sync "
            f"(a conta dedicada `sync-bq-postgres@`, não a compartilhada pelos 29 serviços)."
        )
        raise


def _load_eligible_fixture_ids(
    pg_conn, schema: str, table_name: str, days: int, days_ahead: int | None = None
) -> set:
    """Conjunto de fixture_id com kickoff dentro da faixa de retenção de produto.

    A faixa é [agora - days, agora + days_ahead]; sem `days_ahead`, só o corte para trás.
    Consulta o Postgres de DESTINO (`table_name`, ex. fact_fixtures), já sincronizado nesta
    mesma execução — não reconsulta o BigQuery. É chamada uma vez por tabela que usa a regra
    'fixture_window' (nunca por linha), então a execução de DEV faz até 7 consultas baratas, uma
    por tabela de produto; sem cache. `run_sync._assert_dev_retention_order` garante que
    `table_name` já foi sincronizada nesta run antes desta consulta rodar.
    """
    agora = datetime.now(timezone.utc)
    inicio = agora - timedelta(days=days)
    with pg_conn.cursor() as cur:
        if days_ahead is None:
            cur.execute(
                f'SELECT fixture_id FROM "{schema}"."{table_name}" WHERE kickoff_utc >= %s',
                (inicio,),
            )
        else:
            cur.execute(
                f'SELECT fixture_id FROM "{schema}"."{table_name}" '
                f"WHERE kickoff_utc >= %s AND kickoff_utc <= %s",
                (inicio, agora + timedelta(days=days_ahead)),
            )
        return {row[0] for row in cur.fetchall()}


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
                last_synced_at timestamptz NOT NULL DEFAULT now(),
                regra_versao text
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


_SEM_COLUNA_DE_VERSAO = object()


def _tem_coluna_regra_versao(pg_conn, schema: str) -> bool:
    """A coluna `regra_versao` existe no estado de sincronização? (SQL administrativo da DE#109,
    `scripts/sql/sync_state_regra_versao.sql`; o sync NÃO faz DDL no estado.)"""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = %s "
            "AND table_name = '_sync_state' AND column_name = 'regra_versao'",
            (schema,),
        )
        return cur.fetchone() is not None


def _exige_coluna_regra_versao(schema: str, table_name: str | None = None) -> None:
    """Levanta com a instrução do SQL administrativo (a coluna não existe)."""
    quem = f"{table_name}: a" if table_name else "A"
    raise RuntimeError(
        f"{quem} regra de retenção das odds é versionada (coluna `regra_versao` do estado de "
        f"sincronização) e a coluna não existe em {schema}._sync_state. Aplique "
        f"scripts/sql/sync_state_regra_versao.sql por psycopg (SQL administrativo) ANTES da "
        f"imagem. Nada foi tocado."
    )


def _read_regra_versao(pg_conn, table_name: str, schema: str):
    """Versão da regra gravada junto do estado da tabela, ou None (nunca carimbada)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT regra_versao FROM {_sync_state_table(schema)} WHERE table_name = %s",
            (table_name,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _copia_linhas(pg_conn, cur, alvo: str, column_list: str, columns: list, dados) -> int:
    """COPY tipado de `dados` para `alvo` (já qualificado e citado), com o flush periódico da
    DE#110. Usado pela carga no lugar, pela sombra da troca e pela temporária do staged."""
    row_count = 0
    with cur.copy(f"COPY {alvo} ({column_list}) FROM STDIN") as copy:
        for row in dados:
            copy.write_row([_format_value(row[c]) for c in columns])
            row_count += 1
            if row_count % COPY_FLUSH_EVERY_ROWS == 0:
                _flush_copy_buffer(pg_conn)
    return row_count


def _grava_estado(
    cur, schema: str, table_name: str, bq_modified, regra_versao=_SEM_COLUNA_DE_VERSAO
) -> None:
    """Avança o estado de sincronização. Na troca e no staged roda DENTRO da transação da
    troca: se a troca não acontece, o estado não avança e o detector de atraso enxerga.

    `regra_versao` (DE#109): só as odds a passam (None = sem regra, que também é gravado, para o
    rollback do cache ser detectável). Sem ela o SQL é o de sempre, byte a byte."""
    if regra_versao is not _SEM_COLUNA_DE_VERSAO:
        cur.execute(
            f"""
            INSERT INTO {_sync_state_table(schema)}
                (table_name, last_synced_bq_modified_time, last_synced_at, regra_versao)
            VALUES (%s, %s, now(), %s)
            ON CONFLICT (table_name) DO UPDATE
                SET last_synced_bq_modified_time = EXCLUDED.last_synced_bq_modified_time,
                    last_synced_at = EXCLUDED.last_synced_at,
                    regra_versao = EXCLUDED.regra_versao
            """,
            (table_name, bq_modified, regra_versao),
        )
        return
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
    env: str = "prd",
    sport: str = "nba",
    ctx_troca: "troca_mod.ContextoTroca | None" = None,
    cache_serving: frozenset = frozenset(),
) -> dict:
    """Sincroniza uma mart: por padrão TRUNCATE + COPY dentro de uma única transação.

    `cache_serving` (DE#109): tabelas que o workflow ligou no cache de serving de PRD (hoje só
    `fact_odds_snapshot`). Ligada, a tabela é lida por query job filtrado (mercados servidos,
    fixtures dos últimos 30 dias e futuras com todas as janelas, o resto só com o fechamento).

    `ctx_troca` (DE#108): None = tudo como antes (carga no lugar). Com contexto, a tabela pode
    usar a carga por troca, o caminho staged ou cair na carga no lugar com aviso, conforme
    `troca.escolhe_modo`. A troca que não conclui levanta `TrocaFalhou` (vigente intacta, estado
    sem avançar); o `run_sync` a captura por tabela.

    Skip-if-unchanged: se bq.get_table().modified <= last_synced_bq_modified_time
    em _sync_state, pula essa tabela (sem TRUNCATE, sem locking).

    force=True (modo full-resync) ignora o skip-if-unchanged e re-sincroniza a
    tabela mesmo que o BQ não tenha mudado — útil quando o Postgres sofreu drift
    fora do sync (ex.: truncate manual) e o state ficaria pulando indefinidamente.

    Colunas BQ complexas (REPEATED/RECORD) são puladas — o Postgres nativo é
    escalar. O column_list do COPY usa só as escalares, casando com o DDL nativo.

    env/sport resolvem a regra de retenção de DEV (DE#75/#76/#77, DE#106) via
    `retencao.resolve_regra_retencao(sport, env, table_name, cache_serving)`. Com regra (DEV, ou
    PRD nas odds com o cache de serving ligado), a tabela é lida por query job filtrado no
    BigQuery (`filtro_bq`), submetido e ESPERADO antes do TRUNCATE: sem `bigquery.jobs.create`
    (403) ou com o teto de bytes estourado, o passe levanta com a tabela de destino intacta.
    PRD sem o cache e tabela sem regra: rule é None e toda linha vai pro COPY por `list_rows`,
    byte-idêntico ao anterior, sem query job.
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
    bq_table = _iter_table if _iter_table is not None else bq.get_table(table_ref)
    bq_modified = bq_table.modified  # timezone-aware datetime

    # Modo de carga desta tabela. Decidido ANTES do skip-if-unchanged para o item pulado também
    # ecoar o `modo` (o runbook da DE#108 confere o modo de todos os itens da resposta, e numa
    # execução comum a maioria das tabelas é pulada). Sem contexto de troca nada consulta o
    # catálogo e o caminho é o de sempre; com troca habilitada a checagem de dependentes custa
    # umas consultas de catálogo por tabela habilitada, sem lock em tabela de usuário.
    modo, fallback_motivo = troca_mod.escolhe_modo(pg_conn, schema, table_name, ctx_troca)

    # Regra de retenção desta tabela e, só nas odds, a versão dela (DE#109, história 36): o
    # skip-if-unchanged também exige que a versão gravada seja a de agora, senão mudar a lista de
    # mercados ou o corte de 30 dias (ou desligar o cache em PRD) não recarregaria nada.
    rule = resolve_regra_retencao(sport, env, table_name, cache_serving)
    versionada = (sport or "").lower() == "futebol" and table_name == odds_serving.TABELA_ODDS
    versao_esperada = odds_serving.regra_versao(rule) if versionada else None
    tem_coluna_versao = _tem_coluna_regra_versao(pg_conn, schema) if versionada else False
    if versao_esperada is not None and not tem_coluna_versao:
        _exige_coluna_regra_versao(schema, table_name)
    versao_gravada = (
        _read_regra_versao(pg_conn, table_name, schema) if versionada and tem_coluna_versao else None
    )

    last_synced = _read_last_synced(pg_conn, table_name, schema)
    if (
        not force
        and last_synced is not None
        and bq_modified <= last_synced
        and versao_gravada == versao_esperada
    ):
        logger.info(
            f"Skip {table_name}: BQ não mudou (modified={bq_modified.isoformat()}, "
            f"last_synced={last_synced.isoformat()}, modo={modo})"
        )
        pulada = {"table": table_name, "rows": 0, "skipped": True, "modo": modo}
        if fallback_motivo:
            pulada["fallback_motivo"] = fallback_motivo
        return pulada

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

    if rule is None:
        # PRD sem cache de serving e tabela sem regra: a tabela inteira, por tabledata.list
        # (gratuito, sem job).
        dados = rows_iter
    else:
        agora = datetime.now(timezone.utc)
        eligible_fixture_ids = None
        if rule["kind"] == "fixture_window":
            eligible_fixture_ids = _load_eligible_fixture_ids(
                pg_conn, schema, rule["requires"], rule["days"], rule.get("days_ahead")
            )
        elif rule["kind"] == "cache_serving":
            # Primeiro job (barato): as fixtures elegíveis, do BigQuery, não do Postgres de PRD
            # (o kickoff é o de `fact_fixtures`, e a lista não depende de a tabela ter trocado
            # nesta execução). Lista vazia aborta antes do TRUNCATE.
            eligible_fixture_ids = odds_serving.le_fixtures_elegiveis(
                bq, f"{BIGQUERY_PROJECT_ID}.{dataset}.{rule['fixtures_table']}", rule, agora,
                maximo_bytes_faturados=_teto_de_bytes_faturados(None),
            )
        filtro = _filtro_da_regra(rule, all_fields, agora, eligible_fixture_ids)
        # Submete e espera o job AGORA, antes do TRUNCATE: 403 de IAM ou teto de bytes
        # estourado levantam com a tabela de destino intacta.
        dados = le_tabela_filtrada(
            bq, table_ref, columns, filtro,
            maximo_bytes_faturados=_teto_de_bytes_faturados(
                getattr(bq_table, "num_bytes", None)
            ),
        )

    def copiar(cur, alvo):
        return _copia_linhas(pg_conn, cur, alvo, column_list, columns, dados)

    def atualiza_estado(cur):
        if tem_coluna_versao and versionada:
            _grava_estado(cur, schema, table_name, bq_modified, versao_esperada)
        else:
            _grava_estado(cur, schema, table_name, bq_modified)

    inicio = time.monotonic()
    extra: dict = {}
    if modo == troca_mod.MODO_TROCA:
        extra = troca_mod.carrega_por_troca(
            pg_conn, schema, table_name, ctx_troca, copiar=copiar, atualiza_estado=atualiza_estado
        )
        row_count = extra.pop("rows")
    elif modo == troca_mod.MODO_STAGED:
        extra = troca_mod.carrega_staged(
            pg_conn, schema, table_name, ctx_troca, copiar=copiar, atualiza_estado=atualiza_estado
        )
        row_count = extra.pop("rows")
    else:
        with pg_conn.cursor() as cur:
            # BEGIN é implícito quando autocommit=False; TRUNCATE + COPY + state-update
            # ficam numa única transação. Se qualquer passo falhar, rollback total
            # mantém o state consistente com o dado.
            cur.execute(f'TRUNCATE TABLE "{schema}"."{table_name}"')
            # COPY TIPADO: write_row recebe a tupla nativa (None vira NULL, '' fica '').
            # Streaming linha-a-linha — não materializa a tabela inteira em memória.
            row_count = copiar(cur, f'"{schema}"."{table_name}"')
            atualiza_estado(cur)
        pg_conn.commit()

    duracao_s = round(time.monotonic() - inicio, 2)
    resultado = {
        "table": table_name,
        "rows": row_count,
        "skipped": False,
        "modo": modo,
        "duracao_s": extra.get("duracao_s", duracao_s),
    }
    if "tentativas" in extra:
        resultado["tentativas"] = extra["tentativas"]
        resultado["troca_ms"] = extra["troca_ms"]
    if fallback_motivo:
        resultado["fallback_motivo"] = fallback_motivo
    logger.info(
        f"OK {table_name}: {row_count} linhas (modo={modo}, duracao_s={resultado['duracao_s']}"
        f"{', tentativas=%s, troca_ms=%s' % (extra['tentativas'], extra['troca_ms']) if 'tentativas' in extra else ''})"
    )
    return resultado


def _assert_dev_retention_order(sport: str, env: str, resolved: list[str]) -> None:
    """Falha explícita se uma tabela com regra 'fixture_window' for sincronizada sem
    sua dependência ('requires') na MESMA execução (DE#77, DE#106 história 9).

    Sem esta checagem, sincronizar só `fact_insumos_medidos` (ou outra tabela de produto) em
    DEV, sem `fact_fixtures` na mesma run, produziria um filtro silenciosamente vazio: a
    consulta de fixtures elegíveis rodaria contra o Postgres com `fact_fixtures`
    desatualizada (ou ausente), sem nenhum aviso. Só importa em DEV — em PRD nenhuma
    regra é aplicada.
    """
    if (env or "").lower() != "dev":
        return
    for table in resolved:
        rule = resolve_regra_retencao(sport, env, table)
        if rule is None:
            continue
        requires = rule.get("requires")
        if requires and requires not in resolved:
            raise RuntimeError(
                f"'{table}' usa a regra de retenção de DEV '{rule['kind']}', que depende "
                f"de '{requires}' sincronizada na MESMA execução do sync. Inclua "
                f"'{requires}' em `tables` ou rode com tables='all'."
            )


# ============================================================
# Orquestrador
# ============================================================
def run_sync(
    tables: str | Iterable[str] | None = None,
    env: str = "prd",
    force: bool = False,
    sport: str = "nba",
    troca: str | Iterable[str] | None = None,
    staged: str | Iterable[str] | None = None,
    cache_serving: str | Iterable[str] | None = None,
) -> dict:
    """Executa o sync. Roda pre-flight de schema parity antes de qualquer TRUNCATE.

    Args:
        tables: 'all' (default), CSV string, ou lista. Filtragem preserva
                ordem canônica (dim -> fact -> derivada).
        env: 'prd' (default) ou 'dev'. Determina qual SUPABASE_PG_URL_* usar.
        force: True ignora o skip-if-unchanged e força full-resync de todas as
               tabelas resolvidas (recupera de drift no Postgres feito fora do sync).
        sport: 'nba' (default) ou 'futebol'. Resolve dataset BQ + schema Postgres +
               allowlist (menos as exclusões) via alvo.resolve_alvo_sync().
        troca: tabelas (CSV ou lista) habilitadas na CARGA POR TROCA (DE#108). Vazio/None
               (default) = nenhuma: carga no lugar, como antes. Só futebol; nunca
               `fact_odds_snapshot` em PRD sem o cache de serving (DE#109). Quem escolhe é o
               workflow, por ambiente.
        staged: tabelas habilitadas no caminho STAGED (as com dependente, ex. premissas).
        cache_serving: tabelas (CSV ou lista) que o workflow liga no CACHE DE SERVING de PRD
               (DE#109, ADR 0006): hoje só `fact_odds_snapshot`, que passa a carregar só os
               mercados servidos, as fixtures dos últimos 30 dias e futuras com todas as janelas
               e o resto só com o fechamento (T-15m). Vazio (default) = o PRD completo, como
               antes. Só PRD e só futebol; em DEV o filtro de mercados já vale sempre.

    Returns:
        {status, sport, env, synced: [...], drift: [...], summary, dev_size_mb}
        Cada item de `synced` ecoa `modo` (troca | staged | no_lugar | no_lugar_fallback) --
        inclusive o pulado por BQ inalterado, que ecoa o modo que seria usado --, e o carregado
        traz também `duracao_s` e, quando há troca, `tentativas` e `troca_ms`.
        Se uma tabela habilitada não conseguir trocar (teto de espera esgotado, formato
        divergente, dependente novo, orçamento de retentativas), as demais seguem e o retorno
        é status='swap_failed' com `falhas: [{table, motivo}]` (a vigente fica intacta e o
        estado da tabela não avança). Parity check e IAM continuam abortando o sync INTEIRO.
        (`dev_size_mb`: soma dos bancos do cluster em MiB, medida ao fim do passe DEV
        bem-sucedido; None em PRD e quando a medição falha — DE#106.)
        Em caso de drift detectada no pre-flight, NÃO faz TRUNCATE em nenhuma
        tabela; retorna status='aborted_schema_drift' com o detalhe.
        Se já há outro sync do mesmo (sport, env) com a trava (DE#107), não toca em nada
        e retorna status=STATUS_OCUPADO ('busy') com synced=[].
    """
    dataset, schema, tables_ordered = resolve_alvo_sync(sport)
    pg_url = get_pg_url(env)
    verifica_pooler_de_sessao(pg_url)
    resolved = resolve_tables(tables, tables_ordered)
    _assert_dev_retention_order(sport, env, resolved)
    selecao_troca = troca_mod.parse_lista(troca)
    selecao_staged = troca_mod.parse_lista(staged)
    troca_mod.valida_selecao(sport, selecao_troca, selecao_staged, resolved)
    selecao_cache = troca_mod.parse_lista(cache_serving)
    odds_serving.valida_cache_serving(
        sport, env, selecao_cache, selecao_troca, selecao_staged, resolved
    )
    # None = nenhuma tabela habilitada: o caminho de carga é byte-idêntico ao anterior.
    ctx_troca = (
        troca_mod.novo_contexto(selecao_troca, selecao_staged)
        if (selecao_troca or selecao_staged)
        else None
    )
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

    trava_obtida = False
    try:
        # Trava por (sport, env) ANTES de qualquer outra coisa no destino (nem o
        # CREATE TABLE do _sync_state, nem o SET): ocupada = volta sem tocar em nada.
        trava_obtida = tenta_trava(pg_conn, sport, env)
        if not trava_obtida:
            logger.warning(
                f"Sync sport={sport} env={env} já em andamento (trava ocupada); "
                f"esta execução não toca em nada"
            )
            return {
                "status": STATUS_OCUPADO,
                "sport": sport,
                "env": env,
                "synced": [],
                "drift": [],
            }

        # Override por sessão do statement_timeout=2min que o Supabase seta no
        # database: COPY de marts grandes excede 2min (sobretudo no compute menor
        # do DEV). Acompanha o timeout do Cloud Run (3600s, ver trava.py); não
        # altera nada global.
        with pg_conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{SYNC_STATEMENT_TIMEOUT_S}s'")
        pg_conn.commit()

        # Sombras `__new`/`__old` de uma execução anterior que morreu: removidas AGORA, já com a
        # trava em mãos (nunca sem ela) e antes do parity check, para um aborto não as deixar
        # ocupando disco. Roda mesmo com a troca desligada (rollback por workflow não pode
        # abandonar uma sombra de centenas de MB). Aviso, não aborto.
        avisos = troca_mod.limpa_sombras(
            pg_conn, schema, ctx_troca or troca_mod.ContextoTroca()
        )

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

        # A retenção de DEV e o cache de serving de PRD leem por query job: provar a permissão
        # agora, antes de qualquer carga. Só se alguma tabela da execução tem regra (PRD sem o
        # cache ligado e NBA nunca precisam).
        if any(
            resolve_regra_retencao(sport, env, t, selecao_cache) is not None for t in resolved
        ):
            _verifica_query_job(bq)

        _ensure_sync_state_table(pg_conn, schema)

        # A regra das odds é versionada no estado: sem a coluna (SQL administrativo esquecido) o
        # sync INTEIRO aborta agora, antes de recarregar as tabelas que vêm antes das odds.
        if (sport or "").lower() == "futebol" and any(
            t == odds_serving.TABELA_ODDS
            and odds_serving.regra_versao(resolve_regra_retencao(sport, env, t, selecao_cache))
            is not None
            for t in resolved
        ):
            if not _tem_coluna_regra_versao(pg_conn, schema):
                logger.error(
                    f"Sync abortado ANTES de qualquer TRUNCATE: falta a coluna regra_versao em "
                    f"{schema}._sync_state (sport={sport}, env={env})."
                )
                _exige_coluna_regra_versao(schema)

        synced: list[dict] = []
        falhas: list[dict] = []
        for table in resolved:
            try:
                result = _sync_one_table(
                    bq, pg_conn, table, dataset, schema, tables_ordered,
                    force=force, env=env, sport=sport, ctx_troca=ctx_troca,
                    cache_serving=selecao_cache,
                )
            except troca_mod.TrocaFalhou as e:
                # Uma tabela que não trocou NÃO derruba as seguintes: a vigente está intacta e o
                # estado dela não avançou (o detector de atraso a enxerga). Só TrocaFalhou é
                # capturada aqui: erro de leitura do BigQuery ou de COPY segue abortando.
                pg_conn.rollback()
                logger.error(f"Troca falhou em {e.table}: {e.motivo} {e.detalhe}")
                falhas.append({"table": e.table, "motivo": e.motivo})
                continue
            synced.append(result)

        n_synced = sum(1 for r in synced if not r.get("skipped"))
        n_skipped = sum(1 for r in synced if r.get("skipped"))
        # Conta só as CARREGADAS: a pulada ecoa o modo mas não carregou nada (contá-la faria uma
        # tabela em fallback aparecer uma vez por hora no resumo diário sem ter sido carregada).
        por_modo = {
            m: sum(1 for r in synced if not r.get("skipped") and r.get("modo") == m)
            for m in (
                troca_mod.MODO_TROCA, troca_mod.MODO_STAGED,
                troca_mod.MODO_NO_LUGAR, troca_mod.MODO_FALLBACK,
            )
        }
        logger.info(
            f"Sync sport={sport} env={env} concluído: {n_synced} tabela(s) "
            f"sincronizada(s), {n_skipped} pulada(s) por BQ inalterado, "
            f"modos={por_modo}, falhas de troca={[f['table'] for f in falhas]}"
        )

        # Tamanho do DEV (DE#106): medido AQUI, depois de todas as tabelas carregadas e com a
        # conexão ainda aberta, para não confundir o pico de uma recarga com o tamanho de
        # repouso. Só em DEV; nunca derruba o sync (falha vira None).
        dev_size_mb = (
            medir_tamanho_dev_mb(pg_conn) if (env or "").lower() == "dev" else None
        )

        return {
            "status": troca_mod.STATUS_TROCA_FALHOU if falhas else "success",
            "sport": sport,
            "env": env,
            "synced": synced,
            "drift": [],
            "falhas": falhas,
            "avisos": avisos,
            "summary": {
                "synced": n_synced,
                "skipped": n_skipped,
                "fallback": por_modo[troca_mod.MODO_FALLBACK],
                "falhas_de_troca": len(falhas),
                # O workflow leva estes campos ao log_completion e o resumo diário os mostra
                # (DE#108, histórias 9/20/27/54): quais tabelas caíram no fallback e quanto cada
                # troca durou. Só as CARREGADAS; a pulada não trocou nada.
                "fallback_tabelas": [
                    r["table"] for r in synced
                    if not r.get("skipped") and r.get("modo") == troca_mod.MODO_FALLBACK
                ],
                "trocas": [
                    {
                        "table": r["table"], "modo": r["modo"],
                        "troca_ms": r["troca_ms"], "tentativas": r["tentativas"],
                    }
                    for r in synced
                    if not r.get("skipped") and "troca_ms" in r
                ],
            },
            "dev_size_mb": dev_size_mb,
        }

    except Exception:
        pg_conn.rollback()
        raise
    finally:
        if trava_obtida:
            solta_trava(pg_conn, sport, env)
        pg_conn.close()
