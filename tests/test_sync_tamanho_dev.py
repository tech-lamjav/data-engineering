"""Medição do tamanho do DEV ao fim do passe DEV do sync (DE#106, histórias 11-18).

O limite de 500 MB do plano free do Supabase vale para a SOMA de todos os bancos do cluster; é
essa a conta que a função faz, e o fake de cursor abaixo responde pelo texto do SQL com um mini
modelo disso (uma consulta que olhasse só o banco corrente devolveria menos e o teste pegaria).
A prova contra um Postgres de verdade está em `tests/test_sync_tamanho_dev_integracao.py`.
"""
import pytest

from src.config import FUTEBOL_SYNC_TABLES_ORDERED
from src.sync import bq_to_postgres as sync
from src.sync.tamanho_dev import medir_tamanho_dev_mb

MIB = 1024 * 1024


class _Servidor:
    """Um cluster com vários bancos. Responde só ao que o teste precisa."""

    def __init__(self, bancos=None, erro=None):
        self.bancos = (
            bancos
            if bancos is not None
            else {"postgres": 400 * MIB, "_supabase": 90 * MIB, "template1": 8 * MIB}
        )
        self.erro = erro
        self.log: list[str] = []
        self.rollbacks = 0


class _Cursor:
    def __init__(self, servidor):
        self._s = servidor
        self._linha = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._s.log.append(sql)
        if "pg_database_size" in sql:
            if self._s.erro:
                raise self._s.erro
            if "current_database" in sql:
                self._linha = (self._s.bancos["postgres"],)
            elif "pg_database" in sql:
                self._linha = (sum(self._s.bancos.values()) if self._s.bancos else None,)
        elif "pg_try_advisory_lock" in sql or "pg_advisory_unlock" in sql:
            self._linha = (True,)
        else:
            self._linha = None

    def fetchone(self):
        return self._linha

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, servidor):
        self.servidor = servidor
        self.autocommit = True
        self.commits = 0

    def cursor(self):
        return _Cursor(self.servidor)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.servidor.rollbacks += 1

    def close(self):
        pass


def test_tamanho_e_a_soma_de_todos_os_bancos_em_mb():
    conn = _Conn(_Servidor())

    assert medir_tamanho_dev_mb(conn) == 498.0  # 400 + 90 + 8, e não só os 400 do corrente


def test_tamanho_arredonda_para_uma_casa():
    conn = _Conn(_Servidor(bancos={"postgres": int(451.26 * MIB)}))

    assert medir_tamanho_dev_mb(conn) == 451.3


def test_falha_de_medicao_devolve_nulo_sem_levantar_e_deixa_a_conexao_usavel():
    servidor = _Servidor(erro=RuntimeError("permission denied for pg_database_size"))
    conn = _Conn(servidor)

    assert medir_tamanho_dev_mb(conn) is None
    assert servidor.rollbacks == 1  # transação abortada não pode ficar presa à sessão do sync


def test_soma_nula_vira_nulo_e_nao_zero():
    """Zero MB diria 'DEV vazio, tudo bem'; sem leitura tem de ser 'não medido'."""
    conn = _Conn(_Servidor(bancos={}))

    assert medir_tamanho_dev_mb(conn) is None


# ------------------------------------------------------------------
# A execução do sync mede só em DEV, ao fim, e põe o número no resumo
# ------------------------------------------------------------------
@pytest.fixture
def run(monkeypatch):
    servidor = _Servidor()
    ordem: list[str] = []

    class _Bq:
        pass

    def _sync_one(bq, pg_conn, table, *a, **kw):
        ordem.append(f"tabela:{table}")
        return {"table": table, "rows": 1, "skipped": False}

    original = sync.medir_tamanho_dev_mb

    def _mede(conn):
        ordem.append("medida")
        return original(conn)

    monkeypatch.setattr(sync, "get_pg_url", lambda env: "postgresql://fake:5432/db")
    monkeypatch.setattr(sync.bigquery, "Client", lambda **kw: _Bq())
    monkeypatch.setattr(sync.psycopg, "connect", lambda *a, **kw: _Conn(servidor))
    monkeypatch.setattr(sync, "check_schema_parity", lambda *a, **kw: [])
    monkeypatch.setattr(sync, "_sync_one_table", _sync_one)
    monkeypatch.setattr(sync, "_ensure_sync_state_table", lambda *a, **kw: None)
    monkeypatch.setattr(sync, "medir_tamanho_dev_mb", _mede)
    return servidor, ordem


def test_dev_devolve_o_tamanho_no_resumo(run):
    servidor, _ = run

    resultado = sync.run_sync(tables="all", env="dev", sport="futebol")

    assert resultado["status"] == "success"
    assert resultado["dev_size_mb"] == 498.0


def test_a_medicao_acontece_depois_de_todas_as_tabelas_carregadas(run):
    _, ordem = run

    sync.run_sync(tables="all", env="dev", sport="futebol")

    assert ordem[-1] == "medida"
    assert ordem.count("medida") == 1
    assert len([o for o in ordem if o.startswith("tabela:")]) == 22  # alvo: 23 menos o de-vig


def test_prd_nao_mede_e_o_campo_sai_nulo(run):
    servidor, ordem = run

    resultado = sync.run_sync(tables="all", env="prd", sport="futebol")

    assert "medida" not in ordem
    assert not any("pg_database_size" in s for s in servidor.log)
    assert resultado["dev_size_mb"] is None


def test_falha_da_medicao_nao_derruba_o_sync_dev(run):
    servidor, _ = run
    servidor.erro = RuntimeError("canceling statement due to statement timeout")

    resultado = sync.run_sync(tables="all", env="dev", sport="futebol")

    assert resultado["status"] == "success"
    assert resultado["dev_size_mb"] is None


def test_dev_com_drift_nao_mede(run, monkeypatch):
    servidor, ordem = run
    monkeypatch.setattr(
        sync, "check_schema_parity",
        lambda *a, **kw: [{"table": "x", "kind": "type_mismatch", "detail": "d"}],
    )

    resultado = sync.run_sync(tables="all", env="dev", sport="futebol")

    assert resultado["status"] == "aborted_schema_drift"
    assert "medida" not in ordem
    assert "dev_size_mb" not in resultado


def test_a_lista_de_tabelas_do_alvo_tem_o_numero_que_o_teste_acima_supoe():
    from src.sync.alvo import resolve_alvo_sync

    assert len(FUTEBOL_SYNC_TABLES_ORDERED) == 23
    assert len(resolve_alvo_sync("futebol")[2]) == 22
