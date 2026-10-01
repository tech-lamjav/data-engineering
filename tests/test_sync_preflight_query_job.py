"""Pré-voo de IAM do query job no passe DEV do sync (DE#106).

A retenção de DEV passou a ler por query job, que exige `bigquery.jobs.create` na conta de
runtime (o `list_rows` não exigia). Sem o pré-voo, a falta da permissão só apareceria na
primeira tabela com regra (a sétima na ordem do sync): as seis anteriores já teriam sido
esvaziadas e recarregadas e o workflow ainda repetiria a chamada em 5xx. Falha de IAM tem de
abortar o sync INTEIRO, antes de qualquer TRUNCATE, com a mesma forma do aborto do parity check.
Um dry-run também exige a permissão e não custa nada.
"""
import pytest

from src.sync import bq_to_postgres as sync

MIB = 1024 * 1024


class _Forbidden(Exception):
    pass


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._conn.log.append(("SQL", sql))

    def fetchone(self):
        return (True,)

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, log):
        self.log = log
        self.autocommit = True

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def run(monkeypatch):
    log: list = []

    class _Bq:
        def query(self, sql, job_config=None):
            log.append(("QUERY", sql, bool(job_config.dry_run)))
            if _Bq.erro:
                raise _Bq.erro

        erro = None

    def _sync_one(bq, pg_conn, table, *a, **kw):
        log.append(("TABELA", table))
        return {"table": table, "rows": 1, "skipped": False}

    monkeypatch.setattr(sync, "get_pg_url", lambda env: "postgresql://fake:5432/db")
    monkeypatch.setattr(sync.bigquery, "Client", lambda **kw: _Bq())
    monkeypatch.setattr(sync.psycopg, "connect", lambda *a, **kw: _Conn(log))
    monkeypatch.setattr(sync, "check_schema_parity", lambda *a, **kw: [])
    monkeypatch.setattr(sync, "_sync_one_table", _sync_one)
    monkeypatch.setattr(sync, "_ensure_sync_state_table", lambda *a, **kw: None)
    monkeypatch.setattr(sync, "medir_tamanho_dev_mb", lambda conn: 400.0)
    return log, _Bq


def _eventos(log, tipo):
    return [i for i, e in enumerate(log) if e[0] == tipo]


def test_dev_com_regra_prova_a_permissao_antes_da_primeira_tabela(run):
    log, _ = run

    sync.run_sync(tables="all", env="dev", sport="futebol")

    preflight = _eventos(log, "QUERY")
    assert len(preflight) == 1
    assert preflight[0] < _eventos(log, "TABELA")[0]
    assert log[preflight[0]][2] is True  # dry-run: exige a permissão e não custa nada


def test_sem_permissao_aborta_o_sync_inteiro_sem_carregar_nenhuma_tabela(run):
    log, bq = run
    bq.erro = _Forbidden("403 bigquery.jobs.create")

    with pytest.raises(_Forbidden):
        sync.run_sync(tables="all", env="dev", sport="futebol")

    assert _eventos(log, "TABELA") == []  # nem a primeira tabela, que não tem regra


def test_o_aborto_por_iam_acontece_antes_do_ensure_do_estado_e_de_qualquer_carga(run):
    log, bq = run
    bq.erro = _Forbidden("403")

    with pytest.raises(_Forbidden):
        sync.run_sync(tables="all", env="dev", sport="futebol")

    assert not [e for e in log if e[0] == "SQL" and "TRUNCATE" in e[1]]


def test_prd_nunca_faz_o_pre_voo_nem_precisa_da_permissao(run):
    log, bq = run
    bq.erro = _Forbidden("403 (PRD nem deveria perguntar)")

    resultado = sync.run_sync(tables="all", env="prd", sport="futebol")

    assert resultado["status"] == "success"
    assert _eventos(log, "QUERY") == []


def test_dev_sem_nenhuma_tabela_com_regra_na_execucao_nao_faz_pre_voo(run):
    """Chamada parcial só com tabelas sem retenção (ex.: dim_teams) segue por list_rows."""
    log, bq = run
    bq.erro = _Forbidden("403 (não deveria perguntar)")

    resultado = sync.run_sync(tables="dim_teams,dim_leagues", env="dev", sport="futebol")

    assert resultado["status"] == "success"
    assert _eventos(log, "QUERY") == []


def test_nba_em_dev_nao_faz_pre_voo(run):
    """O NBA não tem regra de retenção: nada de query job, nada de permissão nova."""
    log, bq = run
    bq.erro = _Forbidden("403 (não deveria perguntar)")

    resultado = sync.run_sync(tables="all", env="dev", sport="nba")

    assert resultado["status"] == "success"
    assert _eventos(log, "QUERY") == []
