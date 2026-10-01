"""Retenção de DEV e medição do tamanho contra um Postgres DE VERDADE (DE#106). Pulado por padrão.

Os testes com fakes (`test_bq_to_postgres_dev_retention.py`, `test_sync_tamanho_dev.py`) provam a
lógica; aqui se prova o SQL que o Postgres de verdade executa:
- a faixa de kickoff -30/+14 sobre `fact_fixtures` (timestamptz) devolve os fixture_id certos;
- a soma de `pg_database_size` sobre todos os bancos do cluster é o que a função devolve (e não
  só o banco corrente);
- `_sync_one_table` de uma tabela de produto, ponta a ponta contra o Postgres, grava no destino
  só o que o BigQuery (falso, já filtrado) entregou e manda ao BigQuery os ids da faixa.

COMO RODAR (Postgres descartável; NUNCA aponte para PRD/DEV: o teste cria e apaga tabelas do
schema `futebol` do cluster apontado):

    docker run -d --rm --name pg-retencao-106 -p 55433:5432 -e POSTGRES_PASSWORD=teste postgres:17
    SYNC_TESTE_PG_URL=postgresql://postgres:teste@localhost:55433/postgres \\
        .venv/bin/python3 -m pytest tests/test_sync_retencao_integracao.py -v
    docker stop pg-retencao-106

O teste recusa qualquer URL que não seja localhost: ele faz DDL e TRUNCATE.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest

URL = os.getenv("SYNC_TESTE_PG_URL")

pytestmark = pytest.mark.skipif(
    not URL, reason="defina SYNC_TESTE_PG_URL (Postgres local descartável) para rodar"
)

if URL:
    assert urlparse(URL).hostname in ("localhost", "127.0.0.1"), (
        "SYNC_TESTE_PG_URL tem de ser localhost: o teste faz DDL e TRUNCATE"
    )

import psycopg  # noqa: E402

from src.sync import bq_to_postgres as sync  # noqa: E402
from src.sync.tamanho_dev import medir_tamanho_dev_mb  # noqa: E402

AGORA = datetime.now(timezone.utc)
MIB = 1024 * 1024


@pytest.fixture
def pg():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS futebol")
        c.execute('DROP TABLE IF EXISTS futebol."fact_fixtures"')
        c.execute('DROP TABLE IF EXISTS futebol."fact_insumos_medidos"')
        c.execute('DROP TABLE IF EXISTS futebol."_sync_state"')
        c.execute(
            'CREATE TABLE futebol."fact_fixtures" (fixture_id bigint, kickoff_utc timestamptz)'
        )
        c.execute(
            'CREATE TABLE futebol."fact_insumos_medidos" (fixture_id bigint, valor double precision)'
        )
    conn = psycopg.connect(URL)
    conn.autocommit = False
    yield conn
    conn.close()


def _fixtures(pg, pares):
    with pg.cursor() as cur:
        for fid, dias in pares:
            cur.execute(
                'INSERT INTO futebol."fact_fixtures" VALUES (%s, %s)',
                (fid, AGORA + timedelta(days=dias)),
            )
    pg.commit()


def test_faixa_de_kickoff_no_postgres_real_devolve_so_os_fixture_id_da_faixa(pg):
    _fixtures(pg, [(1, 20), (2, 10), (3, -40), (4, -5), (5, 13.5), (6, -29.5), (7, -31), (8, 14.5)])

    ids = sync._load_eligible_fixture_ids(pg, "futebol", "fact_fixtures", 30, 14)

    assert ids == {2, 4, 5, 6}


def test_so_para_tras_continua_funcionando_sem_o_limite_a_frente(pg):
    _fixtures(pg, [(1, 20), (2, -5), (3, -40)])

    ids = sync._load_eligible_fixture_ids(pg, "futebol", "fact_fixtures", 30)

    assert ids == {1, 2}


def test_tamanho_do_dev_e_a_soma_de_todos_os_bancos_do_cluster(pg):
    with pg.cursor() as cur:
        cur.execute("SELECT datname, pg_database_size(datname) FROM pg_database")
        bancos = cur.fetchall()
        cur.execute("SELECT pg_database_size(current_database())")
        so_o_corrente = cur.fetchone()[0]
    pg.commit()
    assert len(bancos) > 1  # postgres, template0, template1: o cluster tem mais que o corrente

    medido = medir_tamanho_dev_mb(pg)

    esperado = round(sum(b for _, b in bancos) / MIB, 1)
    assert medido == pytest.approx(esperado, abs=0.2)  # o cluster pode crescer entre as leituras
    assert medido > round(so_o_corrente / MIB, 1)


def test_tamanho_do_dev_deixa_a_conexao_usavel_depois_de_uma_falha(pg):
    # Força uma falha de medição: a conexão vira "transação abortada" e a função tem de curá-la.
    class Quebra:
        def cursor(self):
            return pg_cursor_que_falha(pg)

        def commit(self):
            pg.commit()

        def rollback(self):
            pg.rollback()

    def pg_cursor_que_falha(conn):
        cur = conn.cursor()
        original = cur.execute

        def execute(sql, *a, **kw):
            original("SELECT 1/0")  # aborta a transação do servidor de verdade

        cur.execute = execute
        return cur

    assert medir_tamanho_dev_mb(Quebra()) is None

    with pg.cursor() as cur:  # a sessão continua utilizável (rollback feito)
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)


def test_sync_de_tabela_de_produto_ponta_a_ponta_grava_so_o_que_o_bigquery_filtrou(pg):
    _fixtures(pg, [(10, 10), (20, 20), (30, -40), (40, -5)])

    campo = [MagicMock(), MagicMock()]
    for c, (nome, tipo) in zip(campo, [("fixture_id", "INTEGER"), ("valor", "FLOAT")]):
        c.name, c.mode, c.field_type = nome, "NULLABLE", tipo

    filtradas = [{"fixture_id": 10, "valor": 1.5}, {"fixture_id": 40, "valor": 2.5}]
    resultado = MagicMock()
    resultado.__iter__.side_effect = lambda: iter(filtradas)
    job = MagicMock()
    job.result.return_value = resultado
    tabela_bq = MagicMock(modified=AGORA, num_bytes=1_000_000)
    bq = MagicMock()
    bq.list_rows.return_value = MagicMock(schema=campo, table=tabela_bq)
    bq.query.return_value = job

    sync._ensure_sync_state_table(pg, "futebol")
    resumo = sync._sync_one_table(
        bq, pg, "fact_insumos_medidos", "futebol", "futebol", ["fact_insumos_medidos"],
        env="dev", sport="futebol",
    )

    assert resumo["rows"] == 2
    _, cfg = bq.query.call_args.args[0], bq.query.call_args.kwargs["job_config"]
    ids = next(p for p in cfg.query_parameters if p.name == "ids")
    assert sorted(ids.values) == [10, 40]
    with pg.cursor() as cur:
        cur.execute('SELECT fixture_id, valor FROM futebol."fact_insumos_medidos" ORDER BY 1')
        assert cur.fetchall() == [(10, 1.5), (40, 2.5)]


# ------------------------------------------------------------------
# SQL do job 12 (scripts/sql/job12_purge_so_job_run_details.sql)
# ------------------------------------------------------------------
JOB12_SQL = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "sql", "job12_purge_so_job_run_details.sql"
)

# O pg_cron real não existe na imagem postgres:17; este stub reproduz só o contrato que o SQL usa:
# a tabela `cron.job` e `cron.alter_job(job_id, schedule, command, database, username, active)`.
_STUB_CRON = """
DROP SCHEMA IF EXISTS cron CASCADE;
CREATE SCHEMA cron;
CREATE TABLE cron.job (jobid bigint primary key, jobname text, schedule text, command text, active boolean);
CREATE TABLE cron.job_run_details (runid bigserial primary key, start_time timestamptz);
CREATE FUNCTION cron.alter_job(job_id bigint, schedule text DEFAULT NULL, command text DEFAULT NULL,
                               database text DEFAULT NULL, username text DEFAULT NULL,
                               active boolean DEFAULT NULL) RETURNS void LANGUAGE sql AS $f$
    UPDATE cron.job SET schedule = COALESCE($2, schedule), command = COALESCE($3, command),
                        active = COALESCE($6, active) WHERE jobid = $1;
$f$;
"""


@pytest.fixture
def cron_falso():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(_STUB_CRON)
        c.execute(
            "INSERT INTO cron.job VALUES (12, 'purge-old-snapshots', '0 4 * * *', "
            "'DELETE FROM futebol.fact_odds_snapshot WHERE 1=0; "
            "DELETE FROM cron.job_run_details WHERE 1=0', true)"
        )
    yield
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS cron CASCADE")


def _aplica_job12():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(open(JOB12_SQL, encoding="utf-8").read())


def test_job12_fica_so_com_a_limpeza_do_cron_e_mantem_numero_nome_e_horario(cron_falso):
    _aplica_job12()

    with psycopg.connect(URL) as c:
        jobid, nome, horario, ativo, comando = c.execute(
            "SELECT jobid, jobname, schedule, active, command FROM cron.job WHERE jobid = 12"
        ).fetchone()
    assert (jobid, nome, horario, ativo) == (12, "purge-old-snapshots", "0 4 * * *", True)
    assert "cron.job_run_details" in comando
    assert "futebol" not in comando


def test_job12_nao_toca_em_job_12_que_nao_e_o_purge(cron_falso):
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("UPDATE cron.job SET jobname = 'outro-job' WHERE jobid = 12")

    with pytest.raises(psycopg.errors.RaiseException, match="nada foi alterado"):
        _aplica_job12()

    with psycopg.connect(URL) as c:
        (comando,) = c.execute("SELECT command FROM cron.job WHERE jobid = 12").fetchone()
    assert "fact_odds_snapshot" in comando  # intacto
