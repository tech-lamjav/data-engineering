"""A trava de sync contra um Postgres DE VERDADE (DE#107). Pulado por padrão.

O `tests/test_sync_trava.py` prova a lógica com um servidor falso; este prova que o
`pg_try_advisory_lock` de sessão faz o que a lógica supõe: o segundo `run_sync` volta
ocupado enquanto o primeiro está parado no meio do COPY (TRUNCATE já emitido, transação
aberta), e o lock some quando a conexão cai.

COMO RODAR (Postgres descartável; NUNCA aponte para PRD/DEV: o teste cria e apaga a tabela
`futebol.dim_leagues` e a `futebol._sync_state` do cluster apontado):

    docker run -d --rm --name pg-trava-107 -p 55432:5432 -e POSTGRES_PASSWORD=teste postgres:17
    SYNC_TESTE_PG_URL=postgresql://postgres:teste@localhost:55432/postgres \\
        .venv/bin/python3 -m pytest tests/test_sync_trava_integracao.py -v
    docker stop pg-trava-107

O teste recusa qualquer URL que não seja localhost: ele faz DDL e TRUNCATE.
"""
import os
import threading
from datetime import datetime, timezone
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

from src.config import FUTEBOL_SYNC_TABLES_ORDERED  # noqa: E402
from src.sync import bq_to_postgres as sync  # noqa: E402
from src.sync.trava import STATUS_OCUPADO, solta_trava, tenta_trava  # noqa: E402

TABELA = FUTEBOL_SYNC_TABLES_ORDERED[0]
AGORA = datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)


class _Campo:
    def __init__(self, name):
        self.name = name
        self.mode = "NULLABLE"
        self.field_type = "STRING"


class _LinhasDoBq:
    """list_rows falso: entrega uma linha e PARA no meio do COPY até ser liberado."""

    def __init__(self, parado=None, liberar=None):
        self.schema = [_Campo("a")]
        self.table = type("T", (), {"modified": AGORA})()
        self._parado, self._liberar = parado, liberar

    def __iter__(self):
        yield {"a": "x"}
        if self._parado is not None:
            self._parado.set()
            assert self._liberar.wait(timeout=30), "teste travou: ninguém liberou o COPY"
        yield {"a": "y"}


@pytest.fixture
def banco(monkeypatch):
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS futebol")
        c.execute(f'DROP TABLE IF EXISTS futebol."{TABELA}"')
        c.execute(f'CREATE TABLE futebol."{TABELA}" (a text)')
        c.execute('DROP TABLE IF EXISTS futebol."_sync_state"')
    fila = []

    class _Bq:
        def list_rows(self, ref):
            return fila.pop(0) if fila else _LinhasDoBq()

    monkeypatch.setattr(sync, "get_pg_url", lambda env: URL)
    monkeypatch.setattr(sync.bigquery, "Client", lambda **kw: _Bq())
    monkeypatch.setattr(sync, "check_schema_parity", lambda *a, **kw: [])
    yield fila
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'DROP TABLE IF EXISTS futebol."{TABELA}"')
        c.execute('DROP TABLE IF EXISTS futebol."_sync_state"')


def _advisory_locks():
    with psycopg.connect(URL, autocommit=True) as c:
        return c.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
        ).fetchone()[0]


def test_segundo_run_sync_volta_ocupado_com_o_primeiro_parado_no_meio_do_copy(banco):
    parado, liberar = threading.Event(), threading.Event()
    banco.append(_LinhasDoBq(parado, liberar))
    r1 = {}
    t1 = threading.Thread(
        target=lambda: r1.update(sync.run_sync(tables=TABELA, env="prd", sport="futebol"))
    )
    t1.start()
    try:
        assert parado.wait(timeout=30), "o primeiro sync não chegou ao meio do COPY"
        assert _advisory_locks() == 1

        r2 = {}
        # Sem a trava, este segundo sync emitiria TRUNCATE e BLOQUEARIA no lock exclusivo
        # do primeiro: o join com timeout é o que faz a ausência da trava virar vermelho.
        t2 = threading.Thread(
            target=lambda: r2.update(sync.run_sync(tables=TABELA, env="prd", sport="futebol"))
        )
        t2.start()
        t2.join(timeout=15)
        assert not t2.is_alive(), "o segundo sync ficou preso: tentou gravar (sem trava?)"
        assert r2["status"] == STATUS_OCUPADO
        assert r2["synced"] == []
    finally:
        liberar.set()
        t1.join(timeout=30)

    assert r1["status"] == "success"
    assert r1["synced"][0]["rows"] == 2
    assert _advisory_locks() == 0  # soltou no finally


def test_alvo_diferente_nao_e_bloqueado(banco):
    parado, liberar = threading.Event(), threading.Event()
    banco.append(_LinhasDoBq(parado, liberar))
    t1 = threading.Thread(
        target=lambda: sync.run_sync(tables=TABELA, env="prd", sport="futebol")
    )
    t1.start()
    try:
        assert parado.wait(timeout=30)
        # Mesmo Postgres de teste, mas (sport, env) distinto: a chave é outra. O DEV
        # compartilha a tabela com o PRD aqui, então só provamos que a TRAVA não o barra
        # (o lock de linha/tabela é outra história, é o mesmo banco de teste).
        with psycopg.connect(URL) as c:
            assert tenta_trava(c, "futebol", "dev") is True
            solta_trava(c, "futebol", "dev")
    finally:
        liberar.set()
        t1.join(timeout=30)


def test_lock_de_sessao_sobrevive_a_commit_e_rollback_e_cai_com_a_conexao(banco):
    a = psycopg.connect(URL)
    b = psycopg.connect(URL)
    try:
        assert tenta_trava(a, "futebol", "prd") is True  # tenta_trava já faz commit
        a.rollback()  # o lock de sessão não é desfeito por rollback
        assert tenta_trava(b, "futebol", "prd") is False

        a.close()  # a conexão cai SEM unlock (instância morta): o Postgres solta o lock
        assert tenta_trava(b, "futebol", "prd") is True
    finally:
        b.close()
        if not a.closed:
            a.close()
