"""Resolvedor único do alvo do sync (DE#112, fatia 0, histórias 40-42).

O alvo do sync é "a allowlist do esporte menos as exclusões". Sync, detector de atraso e
gerador do contrato de serving têm de enxergar o MESMO alvo; se um deles voltasse a ler a
allowlist crua, o detector alarmaria por atraso numa tabela que o sync parou de copiar.
"""
import ast
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config import FUTEBOL_SYNC_TABLES_ORDERED, get_sync_target
from src.sync import alvo

REPO_ROOT = Path(__file__).resolve().parent.parent
DEVIG = "int_futebol_odds_devig"


# ------------------------------------------------------------------
# Comportamento do resolvedor
# ------------------------------------------------------------------
def test_futebol_nao_inclui_o_devig_e_mantem_as_demais_na_ordem_canonica():
    dataset, schema, tabelas = alvo.resolve_alvo_sync("futebol")

    assert DEVIG not in tabelas
    assert tabelas == [t for t in FUTEBOL_SYNC_TABLES_ORDERED if t != DEVIG]
    assert len(tabelas) == len(FUTEBOL_SYNC_TABLES_ORDERED) - 1
    # dataset e schema continuam sendo os da config (o resolvedor só tira tabelas).
    assert (dataset, schema) == get_sync_target("futebol")[:2]


def test_nba_fica_identico_ao_da_config():
    assert alvo.resolve_alvo_sync("nba") == get_sync_target("nba")


def test_default_e_nba_como_na_config():
    assert alvo.resolve_alvo_sync() == get_sync_target()


def test_esporte_invalido_continua_levantando_value_error():
    with pytest.raises(ValueError):
        alvo.resolve_alvo_sync("basquete")


def test_toda_exclusao_existe_na_allowlist_do_esporte():
    # Exclusão que não casa com nenhuma tabela é typo: o resolvedor ficaria inofensivo
    # e ninguém perceberia que a tabela continuou sendo copiada.
    for sport, excluidas in alvo.SYNC_EXCLUSOES.items():
        _, _, allowlist = get_sync_target(sport)
        for tabela in excluidas:
            assert tabela in allowlist, f"{sport}: exclusão {tabela!r} fora da allowlist"


def test_exclusao_inexistente_na_allowlist_falha_alto(monkeypatch):
    monkeypatch.setitem(alvo.SYNC_EXCLUSOES, "futebol", frozenset({DEVIG, "tabela_fantasma"}))

    with pytest.raises(ValueError, match="tabela_fantasma"):
        alvo.resolve_alvo_sync("futebol")


def test_exclusao_e_lida_na_hora_da_chamada(monkeypatch):
    monkeypatch.setitem(alvo.SYNC_EXCLUSOES, "futebol", frozenset({DEVIG, "dim_teams"}))

    _, _, tabelas = alvo.resolve_alvo_sync("futebol")

    assert "dim_teams" not in tabelas and DEVIG not in tabelas


def test_lista_devolvida_e_copia(monkeypatch):
    _, _, tabelas = alvo.resolve_alvo_sync("futebol")
    tabelas.append("lixo")

    assert "lixo" not in alvo.resolve_alvo_sync("futebol")[2]


def test_importar_o_resolvedor_nao_carrega_banco_nem_sdk_da_nuvem():
    # Subprocesso: no processo do pytest outros módulos já carregaram psycopg/google.
    codigo = (
        "import sys\n"
        "import src.sync.alvo\n"
        "carregados = [m for m in ('psycopg', 'google.cloud.bigquery') if m in sys.modules]\n"
        "sys.exit(1 if carregados else 0)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", codigo], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------
# Os três consumidores usam o resolvedor (o teste que falha se divergirem)
# ------------------------------------------------------------------
CONSUMIDORES = [
    "src/sync/bq_to_postgres.py",
    "src/monitoring/atraso_sync.py",
    "src/monitoring/contrato_serving.py",
]


def _chamadas_a_get_sync_target(caminho: Path) -> list[int]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    linhas = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            f = no.func
            nome = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            if nome == "get_sync_target":
                linhas.append(no.lineno)
    return linhas


@pytest.mark.parametrize("modulo", CONSUMIDORES)
def test_consumidor_nao_le_a_allowlist_crua(modulo):
    assert _chamadas_a_get_sync_target(REPO_ROOT / modulo) == []


def test_nenhum_codigo_do_repo_le_a_allowlist_crua_fora_do_resolvedor():
    # Cobre consumidor futuro: qualquer src/ ou scripts/ novo que chamar
    # `get_sync_target` direto reabre a divergência. Só o resolvedor e a definição na
    # config podem.
    permitidos = {
        REPO_ROOT / "src" / "sync" / "alvo.py",
        REPO_ROOT / "src" / "config.py",
    }
    infratores = []
    for raiz in ("src", "scripts", "cloud_run"):
        for caminho in (REPO_ROOT / raiz).rglob("*.py"):
            if caminho in permitidos or ".venv" in caminho.parts:
                continue
            if _chamadas_a_get_sync_target(caminho):
                infratores.append(str(caminho.relative_to(REPO_ROOT)))
    assert infratores == []


# ------------------------------------------------------------------
# Comportamento observável de cada consumidor com uma exclusão no resolvedor
# ------------------------------------------------------------------
class _FakeCursor:
    """Cursor que não devolve nada: nenhuma função no pg_proc, nenhum estado de sync.

    Única exceção: a trava de sync (DE#107) é concedida, senão `run_sync` voltaria "ocupado".
    """

    def __init__(self):
        self.executados = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executados.append(sql)

    def fetchall(self):
        return []

    def fetchone(self):
        if self.executados and "pg_try_advisory_lock" in self.executados[-1]:
            return (True,)
        return None


class _FakeConn:
    autocommit = False

    def __init__(self):
        self.fechada = False

    def cursor(self):
        return _FakeCursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.fechada = True


def test_sync_all_nao_enxerga_o_devig_e_o_pede_por_nome_e_erro(monkeypatch):
    from src.sync import bq_to_postgres as sync

    monkeypatch.setattr(sync, "get_pg_url", lambda env: "postgresql://fake")
    monkeypatch.setattr(sync.bigquery, "Client", lambda **kw: object())
    monkeypatch.setattr(sync.psycopg, "connect", lambda *a, **kw: _FakeConn())
    paridade = []
    monkeypatch.setattr(
        sync, "check_schema_parity", lambda bq, conn, resolved, ds, sc: paridade.append(resolved) or []
    )
    monkeypatch.setattr(sync, "_ensure_sync_state_table", lambda conn, schema: None)
    carregadas = []
    monkeypatch.setattr(
        sync,
        "_sync_one_table",
        lambda bq, conn, table, *a, **kw: carregadas.append(table) or {"table": table, "rows": 0, "skipped": True},
    )

    sync.run_sync(tables="all", env="prd", sport="futebol")

    assert DEVIG not in paridade[0]
    assert DEVIG not in carregadas
    assert "fact_odds_snapshot" in carregadas

    # Pedir o devig explicitamente também não passa: não é mais tabela do alvo.
    with pytest.raises(ValueError, match=DEVIG):
        sync.run_sync(tables=DEVIG, env="prd", sport="futebol")


def test_gerador_do_contrato_nao_lista_o_devig_nem_como_orfa():
    from src.monitoring import contrato_serving

    md = contrato_serving.gera(pg_conn=_FakeConn())

    assert DEVIG not in md
    # As demais sem leitor continuam aparecendo (o teste não é vacuamente verde).
    assert "int_futebol_premissas_ou" in md


def test_detector_nao_mede_o_atraso_do_devig(monkeypatch):
    import psycopg
    from google.cloud import bigquery

    from src.monitoring import atraso_sync

    consultadas = []

    class _BqFake:
        def __init__(self, **kw):
            pass

        def get_table(self, ref):
            consultadas.append(ref.split(".")[-1])

            class _T:
                modified = datetime(2026, 10, 1, tzinfo=timezone.utc)

            return _T()

    monkeypatch.setattr(bigquery, "Client", _BqFake)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: _FakeConn())
    monkeypatch.setattr("src.config.get_pg_url_ro", lambda env: "postgresql://fake")
    monkeypatch.setenv("DETECTOR_DRY_RUN", "1")

    atraso_sync.roda_detector()

    assert DEVIG not in consultadas
    assert "fact_odds_snapshot" in consultadas
