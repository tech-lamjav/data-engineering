"""O cache de serving das odds contra um Postgres DE VERDADE (DE#109). Pulado por padrão.

`tests/test_sync_odds_serving.py` prova a regra, o filtro e a versão com fakes; este prova o que os
fakes não conseguem: que o SQL administrativo da coluna `regra_versao` é idempotente e que a
imagem nova ABORTA sem ela; que a versão é gravada junto do estado e governa o skip-if-unchanged
no banco de verdade; e que as odds entram na CARGA POR TROCA com a versão gravada dentro da
transação da troca (sombra, RENAME, estado). O BigQuery continua falso (entrega as linhas que o
BigQuery real entregaria já filtradas).

COMO RODAR (Postgres descartável; NUNCA aponte para PRD/DEV: o teste cria o schema `futebol` e
tabelas no cluster apontado, e o apaga):

    docker run -d --rm --name pg-odds-109 -p 55619:5432 -e POSTGRES_PASSWORD=teste postgres:17
    SYNC_TESTE_PG_URL=postgresql://postgres:teste@localhost:55619/postgres \\
        .venv/bin/python3 -m pytest tests/test_sync_odds_serving_integracao.py -v
    docker stop pg-odds-109

Recusa URL que não seja localhost.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest

URL = os.getenv("SYNC_TESTE_PG_URL")

pytestmark = pytest.mark.skipif(
    not URL, reason="defina SYNC_TESTE_PG_URL (Postgres local descartável) para rodar"
)

if URL:
    assert urlparse(URL).hostname in ("localhost", "127.0.0.1"), (
        "SYNC_TESTE_PG_URL tem de ser localhost: o teste faz DDL"
    )

import psycopg  # noqa: E402

from src.sync import bq_to_postgres as mod  # noqa: E402
from src.sync import odds_serving, retencao, troca  # noqa: E402

ODDS = "fact_odds_snapshot"
LIGADA = frozenset({ODDS})
AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
SQL_ADMIN = Path(__file__).resolve().parent.parent / "scripts" / "sql" / "sync_state_regra_versao.sql"

COLUNAS = ["fixture_id", "market_id", "collection_window"]


def _exec(sql, params=None):
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(sql, params)


def _um(sql, params=None):
    with psycopg.connect(URL, autocommit=True) as c:
        return c.execute(sql, params).fetchone()[0]


@pytest.fixture
def banco():
    """Schema `futebol` (o do SQL administrativo) com o estado de uma instalação ANTIGA: sem a
    coluna `regra_versao`, como em PRD e DEV hoje."""
    _exec("DROP SCHEMA IF EXISTS futebol CASCADE")
    _exec("CREATE SCHEMA futebol")
    _exec(
        'CREATE TABLE futebol."_sync_state" (table_name text PRIMARY KEY, '
        "last_synced_bq_modified_time timestamptz NOT NULL, "
        "last_synced_at timestamptz NOT NULL DEFAULT now())"
    )
    _exec(
        f"CREATE TABLE futebol.{ODDS} (fixture_id bigint, market_id bigint, collection_window text)"
    )
    _exec(f"INSERT INTO futebol.{ODDS} VALUES (999, 1, 'velha')")
    yield
    _exec("DROP SCHEMA IF EXISTS futebol CASCADE")


def _aplica_sql_administrativo():
    _exec(SQL_ADMIN.read_text(encoding="utf-8"))


def _campo(nome, tipo):
    f = MagicMock()
    f.name, f.mode, f.field_type = nome, "NULLABLE", tipo
    return f


class _BQ:
    """BigQuery falso: o job de `fact_fixtures` devolve as elegíveis; o das odds, as filtradas."""

    def __init__(self, odds, modified=AGORA):
        self._odds = odds
        self.queries = []
        schema = [_campo("fixture_id", "INTEGER"), _campo("market_id", "INTEGER"),
                  _campo("collection_window", "STRING")]
        it = MagicMock()
        it.schema = schema
        it.table.modified = modified
        it.table.num_bytes = 1_000
        it.__iter__.side_effect = lambda: iter(_linhas([{"fixture_id": 999, "market_id": 1,
                                                          "collection_window": "tabela-inteira"}]))
        self.list_rows = MagicMock(return_value=it)
        self.query = MagicMock(side_effect=self._query)

    def _query(self, sql, job_config=None):
        self.queries.append(sql)
        dados = [{"fixture_id": 200}] if "fact_fixtures" in sql else self._odds
        resultado = MagicMock()
        resultado.__iter__.side_effect = lambda: iter(_linhas(dados))
        job = MagicMock()
        job.result.return_value = resultado
        return job


def _linhas(dicts):
    out = []
    for d in dicts:
        r = MagicMock()
        r.__getitem__.side_effect = lambda c, _d=d: _d[c]
        out.append(r)
    return out


ODDS_FILTRADAS = [
    {"fixture_id": 200, "market_id": 1, "collection_window": "daily"},
    {"fixture_id": 100, "market_id": 1, "collection_window": "t15m"},
]


def _sync(bq, conn, **kw):
    kw.setdefault("cache_serving", LIGADA)
    return mod._sync_one_table(
        bq, conn, ODDS, dataset="futebol", schema="futebol", tables_ordered=[ODDS],
        env="prd", sport="futebol", **kw,
    )


def _estado():
    with psycopg.connect(URL, autocommit=True) as c:
        return c.execute(
            "SELECT last_synced_bq_modified_time, regra_versao FROM futebol._sync_state "
            "WHERE table_name = %s", (ODDS,)
        ).fetchone()


# ------------------------------------------------------------------
# SQL administrativo
# ------------------------------------------------------------------
def test_o_sql_administrativo_e_idempotente_e_cria_a_coluna_nula(banco):
    _aplica_sql_administrativo()
    _aplica_sql_administrativo()  # segunda vez: não falha

    assert _um(
        "SELECT data_type FROM information_schema.columns WHERE table_schema='futebol' "
        "AND table_name='_sync_state' AND column_name='regra_versao'"
    ) == "text"


def test_sem_a_coluna_a_imagem_nova_aborta_antes_do_truncate_e_a_tabela_fica_intacta(banco):
    with psycopg.connect(URL) as conn, pytest.raises(RuntimeError, match="sync_state_regra_versao.sql"):
        _sync(_BQ(ODDS_FILTRADAS), conn)

    assert _um(f"SELECT count(*) FROM futebol.{ODDS}") == 1  # a linha velha segue lá


def test_o_estado_novo_ja_nasce_com_a_coluna_em_instalacao_nova():
    """`_ensure_sync_state_table` cria a coluna para um banco que nunca teve o estado."""
    _exec("DROP SCHEMA IF EXISTS futebol CASCADE")
    _exec("CREATE SCHEMA futebol")
    try:
        with psycopg.connect(URL) as conn:
            mod._ensure_sync_state_table(conn, "futebol")
            assert mod._tem_coluna_regra_versao(conn, "futebol") is True
    finally:
        _exec("DROP SCHEMA IF EXISTS futebol CASCADE")


# ------------------------------------------------------------------
# Carga no lugar: versão gravada, skip, regra mudada, rollback
# ------------------------------------------------------------------
def test_a_carga_grava_so_o_filtrado_e_a_versao_junto_do_estado(banco):
    _aplica_sql_administrativo()

    with psycopg.connect(URL) as conn:
        r = _sync(_BQ(ODDS_FILTRADAS), conn)

    assert r["rows"] == 2 and r["modo"] == "no_lugar"
    assert _um(f"SELECT count(*) FROM futebol.{ODDS} WHERE collection_window = 'velha'") == 0
    modificado, versao = _estado()
    assert modificado == AGORA
    assert versao == odds_serving.regra_versao(
        retencao.resolve_regra_retencao("futebol", "prd", ODDS, LIGADA)
    )


def test_bq_inalterado_pula_mudar_a_lista_recarrega_e_desligar_o_cache_restaura_a_tabela_completa(
    banco, monkeypatch
):
    _aplica_sql_administrativo()
    with psycopg.connect(URL) as conn:
        _sync(_BQ(ODDS_FILTRADAS), conn)

    # 1. BigQuery inalterado e mesma regra: pula, sem abrir job.
    bq = _BQ(ODDS_FILTRADAS)
    with psycopg.connect(URL) as conn:
        assert _sync(bq, conn)["skipped"] is True
    assert bq.queries == []

    # 2. Mesma data no BigQuery, lista de mercados diferente: recarrega.
    monkeypatch.setattr(retencao, "MERCADOS_SERVIDOS", (1, 4, 5, 6, 8, 12, 45))
    bq = _BQ(ODDS_FILTRADAS[:1])
    with psycopg.connect(URL) as conn:
        r = _sync(bq, conn)
    assert r["skipped"] is False and r["rows"] == 1
    assert _um(f"SELECT count(*) FROM futebol.{ODDS}") == 1
    monkeypatch.undo()

    # 3. Rollback pelo workflow (cache desligado): a versão gravada era a do cache, a de agora é
    # nenhuma, e a tabela completa volta mesmo com o BigQuery inalterado.
    bq = _BQ([])
    with psycopg.connect(URL) as conn:
        r = _sync(bq, conn, cache_serving=frozenset())
    assert r["skipped"] is False
    assert bq.queries == []  # tabledata.list, sem query job
    assert _um(f"SELECT count(*) FROM futebol.{ODDS}") == 1  # a "tabela inteira" do fake
    assert _estado()[1] is None


# ------------------------------------------------------------------
# As odds entram na carga por troca, com o estado e a versão na transação da troca
# ------------------------------------------------------------------
def test_as_odds_entram_na_troca_e_a_versao_e_gravada_na_transacao_da_troca(banco):
    _aplica_sql_administrativo()
    ctx = troca.ContextoTroca(
        troca.ConfigTroca(teto_espera_ms=400, tentativas=2, pausa_min_s=0.0, pausa_max_s=0.0),
        troca={ODDS},
    )

    with psycopg.connect(URL) as conn:
        r = _sync(_BQ(ODDS_FILTRADAS), conn, ctx_troca=ctx)

    assert r["modo"] == "troca" and r["rows"] == 2 and r["tentativas"] == 1
    assert _um(f"SELECT count(*) FROM futebol.{ODDS}") == 2
    assert _estado()[1].startswith("cache-serving/")
    nomes = [
        t for (t,) in psycopg.connect(URL, autocommit=True).execute(
            "SELECT relname FROM pg_class WHERE relnamespace='futebol'::regnamespace "
            "AND relkind='r' ORDER BY 1"
        ).fetchall()
    ]
    assert nomes == ["_sync_state", ODDS]  # nenhuma sombra sobrou
