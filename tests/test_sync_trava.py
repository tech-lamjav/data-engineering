"""Trava de sync concorrente por (sport, env) (DE#107).

Dois `run_sync` do mesmo (sport, env) não podem gravar ao mesmo tempo no mesmo Postgres:
o segundo tem de voltar "ocupado" SEM emitir TRUNCATE nem tocar em nada. A trava é um
`pg_try_advisory_lock` de SESSÃO no Postgres de destino, logo depois do connect.

Estes testes rodam no CI com mock total: um "servidor" falso em memória decide o lock
(set protegido por `threading.Lock`) e cada conexão falsa registra o que executou. O mesmo
cenário contra um Postgres de verdade está em `tests/test_sync_trava_integracao.py`
(pulado por padrão).
"""
import threading
from datetime import datetime, timezone

import pytest

try:
    from src.sync import bq_to_postgres as sync
except Exception as e:  # pragma: no cover
    pytest.skip(f"src.sync.bq_to_postgres não importável: {e}", allow_module_level=True)

from src.config import FUTEBOL_SYNC_TABLES_ORDERED
from src.sync.trava import STATUS_OCUPADO, chave_trava

TABELA = FUTEBOL_SYNC_TABLES_ORDERED[0]
AGORA = datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)


class _ServidorFalso:
    """O Postgres de destino: guarda os advisory locks de sessão por chave."""

    def __init__(self):
        self.locks: dict[str, "_ConnFalsa"] = {}
        self._mutex = threading.Lock()
        self.conexoes: list["_ConnFalsa"] = []

    def conecta(self):
        conn = _ConnFalsa(self)
        self.conexoes.append(conn)
        return conn

    def tenta(self, chave, dono):
        with self._mutex:
            if chave in self.locks and self.locks[chave] is not dono:
                return False
            self.locks[chave] = dono
            return True

    def solta(self, chave, dono):
        with self._mutex:
            if self.locks.get(chave) is dono:
                del self.locks[chave]
                return True
            return False

    def solta_tudo_de(self, dono):
        with self._mutex:
            for k in [k for k, v in self.locks.items() if v is dono]:
                del self.locks[k]


class _CopyFalso:
    def __init__(self, conn, sql):
        self._conn = conn
        conn.log.append(("COPY", sql))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, row):
        self._conn.linhas.append(list(row))


class _CursorFalso:
    def __init__(self, conn):
        self._conn = conn
        self._ultimo = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        c = self._conn
        c.log.append(("SQL", sql, params))
        if "pg_try_advisory_lock" in sql:
            self._ultimo = (c.servidor.tenta(params[0], c),)
        elif "pg_advisory_unlock" in sql:
            self._ultimo = (c.servidor.solta(params[0], c),)
        else:
            self._ultimo = None

    def fetchone(self):
        # `_read_last_synced` (sem linha = nunca sincronizado) e a consulta do lock.
        return self._ultimo

    def fetchall(self):
        return []

    def copy(self, sql):
        return _CopyFalso(self._conn, sql)


class _ConnFalsa:
    def __init__(self, servidor):
        self.servidor = servidor
        self.log: list[tuple] = []
        self.linhas: list[list] = []
        self.autocommit = True
        self.fechada = False

    def cursor(self):
        return _CursorFalso(self)

    def commit(self):
        self.log.append(("COMMIT",))

    def rollback(self):
        self.log.append(("ROLLBACK",))

    def close(self):
        # O Postgres solta o lock de sessão quando a conexão cai.
        self.fechada = True
        self.servidor.solta_tudo_de(self)

    # -- helpers de asserção --
    def sqls(self):
        return [e[1] for e in self.log if e[0] == "SQL"]

    def emitiu(self, trecho):
        return any(trecho in s for s in self.sqls())


class _Campo:
    def __init__(self, name):
        self.name = name
        self.mode = "NULLABLE"
        self.field_type = "STRING"


class _LinhasDoBq:
    """list_rows falso: entrega uma linha e PARA no meio (o "meio do COPY")."""

    def __init__(self, parado=None, liberar=None):
        self.schema = [_Campo("a")]
        self.table = type("T", (), {"modified": AGORA})()
        self._parado = parado
        self._liberar = liberar

    def __iter__(self):
        yield {"a": "x"}
        if self._parado is not None:
            self._parado.set()
            assert self._liberar.wait(timeout=20), "teste travou: ninguém liberou o COPY"
        yield {"a": "y"}


@pytest.fixture
def ambiente(monkeypatch):
    """Servidor falso + BQ falso. Devolve um objeto com `servidor` e `bq_linhas`."""

    class _Amb:
        pass

    amb = _Amb()
    amb.servidor = _ServidorFalso()
    amb.proximas_linhas = []  # fila de _LinhasDoBq entregues por list_rows

    class _BqFalso:
        def list_rows(self, ref):
            if amb.proximas_linhas:
                return amb.proximas_linhas.pop(0)
            return _LinhasDoBq()

    monkeypatch.setattr(sync, "get_pg_url", lambda env: "postgresql://fake:5432/db")
    monkeypatch.setattr(sync.bigquery, "Client", lambda **kw: _BqFalso())
    monkeypatch.setattr(sync.psycopg, "connect", lambda *a, **kw: amb.servidor.conecta())
    monkeypatch.setattr(sync, "check_schema_parity", lambda *a, **kw: [])
    return amb


def _run(env="prd", sport="futebol"):
    return sync.run_sync(tables=TABELA, env=env, sport=sport)


# ------------------------------------------------------------------
# Critério 1: dois run_sync do mesmo (sport, env), o primeiro parado no meio do COPY
# ------------------------------------------------------------------
def test_segundo_run_sync_do_mesmo_alvo_volta_ocupado_sem_truncate(ambiente):
    parado, liberar = threading.Event(), threading.Event()
    ambiente.proximas_linhas = [_LinhasDoBq(parado, liberar)]
    resultado_1 = {}

    t1 = threading.Thread(target=lambda: resultado_1.update(_run()))
    t1.start()
    try:
        assert parado.wait(timeout=20), "o primeiro sync não chegou ao meio do COPY"
        conn_1 = ambiente.servidor.conexoes[0]
        assert conn_1.emitiu("TRUNCATE")  # o primeiro está de fato no meio da carga

        resultado_2 = _run()

        conn_2 = ambiente.servidor.conexoes[1]
        assert resultado_2["status"] == STATUS_OCUPADO
        assert resultado_2["synced"] == []
        assert not conn_2.emitiu("TRUNCATE")
        # "Sem tocar em nada": nem CREATE TABLE, nem SET, nem COPY, só a tentativa do lock.
        assert len(conn_2.sqls()) == 1 and "pg_try_advisory_lock" in conn_2.sqls()[0]
        assert not [e for e in conn_2.log if e[0] == "COPY"]
        assert conn_2.fechada
    finally:
        liberar.set()
        t1.join(timeout=20)

    assert resultado_1["status"] == "success"
    assert resultado_1["synced"][0]["rows"] == 2


def test_a_trava_e_solta_ao_fim_do_sync(ambiente):
    _run()

    assert ambiente.servidor.locks == {}
    conn = ambiente.servidor.conexoes[0]
    assert conn.emitiu("pg_advisory_unlock")  # unlock explícito, sem depender do pooler


def test_a_trava_e_solta_mesmo_quando_o_sync_falha(ambiente, monkeypatch):
    def _explode(*a, **kw):
        raise RuntimeError("COPY quebrou")

    monkeypatch.setattr(sync, "_sync_one_table", _explode)

    with pytest.raises(RuntimeError, match="COPY quebrou"):
        _run()

    assert ambiente.servidor.locks == {}


def test_a_trava_e_por_sport_e_env_ambientes_diferentes_nao_se_bloqueiam(ambiente):
    parado, liberar = threading.Event(), threading.Event()
    ambiente.proximas_linhas = [_LinhasDoBq(parado, liberar)]
    t1 = threading.Thread(target=lambda: _run(env="prd"))
    t1.start()
    try:
        assert parado.wait(timeout=20)
        dev = _run(env="dev")
        nba = sync.run_sync(tables="all", env="prd", sport="nba")
    finally:
        liberar.set()
        t1.join(timeout=20)

    assert dev["status"] == "success"
    assert nba["status"] != STATUS_OCUPADO


def test_trava_ocupada_nao_mexe_nem_no_statement_timeout(ambiente):
    ambiente.servidor.locks[chave_trava("futebol", "prd")] = object()

    resultado = _run()

    assert resultado["status"] == STATUS_OCUPADO
    assert resultado["sport"] == "futebol" and resultado["env"] == "prd"
    assert not ambiente.servidor.conexoes[0].emitiu("statement_timeout")


# ------------------------------------------------------------------
# statement_timeout da sessão sobe junto com o timeout do Cloud Run (DE#112)
# ------------------------------------------------------------------
def test_statement_timeout_da_sessao_acompanha_o_timeout_do_cloud_run(ambiente):
    _run()

    conn = ambiente.servidor.conexoes[0]
    sets = [s for s in conn.sqls() if "statement_timeout" in s]
    assert sets == ["SET statement_timeout = '3600s'"]
    # E só depois de a trava estar em mãos.
    sqls = conn.sqls()
    assert sqls.index(sets[0]) > next(
        i for i, s in enumerate(sqls) if "pg_try_advisory_lock" in s
    )


# ------------------------------------------------------------------
# Pooler em modo transação (6543) quebra o lock de sessão: recusar antes de conectar
# ------------------------------------------------------------------
def test_porta_do_pooler_em_modo_transacao_e_recusada_antes_de_conectar(ambiente, monkeypatch):
    monkeypatch.setattr(
        sync, "get_pg_url", lambda env: "postgresql://u:p@aws-0.pooler.supabase.com:6543/postgres"
    )

    with pytest.raises(RuntimeError, match="6543"):
        _run()

    assert ambiente.servidor.conexoes == []


def test_chave_da_trava_e_deterministica_e_distinta_por_alvo():
    assert chave_trava("futebol", "prd") == chave_trava("futebol", "prd")
    chaves = {
        chave_trava(s, e) for s in ("futebol", "nba") for e in ("prd", "dev")
    }
    assert len(chaves) == 4
