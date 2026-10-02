"""O handler do sync mapeia "ocupado" (trava de DE#107) para HTTP 409.

O workflow trata 409 como "em andamento" (WARNING, fora de `failed_services`). Qualquer
outro status de falha continua sendo 500. `functions_framework` é stubado como nos testes
dos outros wrappers (`test_cloud_run_wrappers.py`).
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

CLOUD_RUN = Path(__file__).resolve().parent.parent / "cloud_run"


class _Args(dict):
    """Como werkzeug: `get(chave, default=...)` aceita `default` por nome."""

    def get(self, key, default=None):
        return super().get(key, default)


class _Req:
    def __init__(self, **args):
        self.args = _Args(args)


@pytest.fixture
def handler(monkeypatch):
    ff = types.ModuleType("functions_framework")
    ff.http = lambda fn: fn
    monkeypatch.setitem(sys.modules, "functions_framework", ff)
    monkeypatch.syspath_prepend(str(CLOUD_RUN / "sync_bq_to_postgres"))
    sys.modules.pop("main", None)
    mod = importlib.import_module("main")
    yield mod
    sys.modules.pop("main", None)


def test_ocupado_vira_409_sem_vazar_detalhe(handler, monkeypatch):
    monkeypatch.setattr(
        handler,
        "run_sync",
        lambda **kw: {"status": "busy", "sport": "futebol", "env": "prd", "synced": [], "drift": []},
    )

    corpo, codigo = handler.sync_bq_to_postgres(_Req(sport="futebol", env="prd"))

    assert codigo == 409
    assert corpo["status"] == "busy"
    assert corpo["sport"] == "futebol" and corpo["env"] == "prd"
    assert "já em andamento" in corpo["message"]


def test_drift_continua_500_e_sucesso_continua_200(handler, monkeypatch):
    monkeypatch.setattr(
        handler,
        "run_sync",
        lambda **kw: {"status": "aborted_schema_drift", "sport": "nba", "env": "dev", "drift": [1], "synced": []},
    )
    _, codigo = handler.sync_bq_to_postgres(_Req(sport="nba", env="dev"))
    assert codigo == 500

    monkeypatch.setattr(
        handler,
        "run_sync",
        lambda **kw: {"status": "success", "sport": "nba", "env": "dev", "synced": [], "summary": {}},
    )
    _, codigo = handler.sync_bq_to_postgres(_Req(sport="nba", env="dev"))
    assert codigo == 200


def test_script_local_sai_com_3_quando_ocupado(monkeypatch):
    """Drift sai com 2; ocupado (outro sync com a trava) sai com 3, distinto de erro (1)."""
    script = importlib.import_module("scripts.sync_bq_to_postgres")
    monkeypatch.setattr(
        script,
        "run_sync",
        lambda **kw: {"status": "busy", "sport": "futebol", "env": "dev", "synced": []},
    )

    assert script.main() == 3


# ------------------------------------------------------------------
# DE#106: o tamanho do DEV (medido ao fim do passe DEV) atravessa o wrapper HTTP
# ------------------------------------------------------------------
def test_sucesso_repassa_o_tamanho_do_dev_no_corpo(handler, monkeypatch):
    monkeypatch.setattr(
        handler,
        "run_sync",
        lambda **kw: {
            "status": "success", "sport": "futebol", "env": "dev", "synced": [],
            "summary": {}, "dev_size_mb": 412.3,
        },
    )

    corpo, codigo = handler.sync_bq_to_postgres(_Req(sport="futebol", env="dev"))

    assert codigo == 200
    assert corpo["dev_size_mb"] == 412.3


def test_sucesso_sem_o_campo_sai_nulo_e_nao_levanta(handler, monkeypatch):
    """PRD (e qualquer resposta de antes do campo existir) não traz `dev_size_mb`."""
    monkeypatch.setattr(
        handler,
        "run_sync",
        lambda **kw: {"status": "success", "sport": "futebol", "env": "prd", "synced": [], "summary": {}},
    )

    corpo, codigo = handler.sync_bq_to_postgres(_Req(sport="futebol", env="prd"))

    assert codigo == 200
    assert corpo["dev_size_mb"] is None
