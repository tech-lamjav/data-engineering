"""Testes dos wrappers Cloud Run (cloud_run/**/main.py).

Cobre os achados do code review no escopo de wrappers:
- #68: resposta de sucesso padronizada como tupla explícita (dict, 200).
- #40: propagação de `mode` nos wrappers de futebol não vaza estado entre
  requisições (env var restaurada no finally).
- #60: notify_execution lê segredos de forma lazy dentro do handler (sem
  KeyError no cold start) e devolve 500 claro quando faltam.

Os testes de #68/#40 são estáticos (AST/leitura de arquivo) para não depender
de `functions_framework`/scripts no ambiente de teste. O de notify_execution
injeta um stub de `functions_framework` e mocka smtplib.
"""
import ast
import importlib
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLOUD_RUN = REPO_ROOT / "cloud_run"

# Wrappers que devolvem `saved_count` chamando o Extractor direto (sem result_code).
EXTRACTOR_DIRECT = {"extract_odds", "extract_predictions", "extract_fixture_lineups"}
# Wrappers "especiais" com forma de retorno própria.
SPECIAL = {"daily_summary", "sync_bq_to_postgres", "notify_execution"}


def _all_main_files():
    return sorted(CLOUD_RUN.rglob("main.py"))


def _success_returns(tree: ast.AST):
    """Retorna os nós Return cujo dict tem status == 'success'."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return):
            continue
        value = node.value
        # Forma (dict, code) -> Tuple; forma dict puro -> Dict
        dict_node = None
        if isinstance(value, ast.Dict):
            dict_node = value
        elif isinstance(value, ast.Tuple) and value.elts and isinstance(value.elts[0], ast.Dict):
            dict_node = value.elts[0]
        if dict_node is None:
            continue
        for k, v in zip(dict_node.keys, dict_node.values):
            if (
                isinstance(k, ast.Constant)
                and k.value == "status"
                and isinstance(v, ast.Constant)
                and v.value in ("success", "notified")
            ):
                found.append(node)
    return found


@pytest.mark.parametrize("main_file", _all_main_files(), ids=lambda p: str(p.relative_to(CLOUD_RUN)))
def test_success_returns_explicit_200_tuple(main_file):
    """#68: todo retorno de sucesso deve ser tupla explícita (dict, <int>)."""
    tree = ast.parse(main_file.read_text())
    returns = _success_returns(tree)
    assert returns, f"nenhum return de sucesso encontrado em {main_file}"
    for node in returns:
        assert isinstance(node.value, ast.Tuple), (
            f"{main_file}: retorno de sucesso deve ser tupla (dict, code), "
            f"não dict puro (linha {node.lineno})"
        )
        assert len(node.value.elts) == 2, f"{main_file}: tupla deve ter (dict, code)"
        code = node.value.elts[1]
        assert isinstance(code, ast.Constant) and code.value == 200, (
            f"{main_file}: código de sucesso deve ser 200 (linha {node.lineno})"
        )


def _futebol_mode_wrappers():
    out = []
    for p in sorted((CLOUD_RUN / "futebol").rglob("main.py")):
        text = p.read_text()
        if "_MODE" in text and "os.environ[" in text:
            out.append(p)
    return out


@pytest.mark.parametrize(
    "main_file", _futebol_mode_wrappers(), ids=lambda p: str(p.relative_to(CLOUD_RUN))
)
def test_mode_env_var_restored_in_finally(main_file):
    """#40: wrappers que mutam <X>_MODE devem restaurar a env var no finally,
    evitando vazamento de estado entre requisições na mesma instância."""
    text = main_file.read_text()
    assert "finally:" in text, f"{main_file}: mutação de _MODE sem finally de restauração"
    assert "_prev_mode" in text, f"{main_file}: deve guardar valor anterior antes de mutar"
    # restaura ou remove a env var
    assert ".pop(" in text or "= _prev_mode" in text, (
        f"{main_file}: finally deve restaurar/remover a env var"
    )


# --- notify_execution (#60): leitura lazy de segredos ---------------------


@pytest.fixture
def notify_module(monkeypatch):
    """Importa cloud_run/notify_execution/main.py com functions_framework stubado."""
    # Stub de functions_framework: decorator .http é no-op.
    ff = types.ModuleType("functions_framework")
    ff.http = lambda fn: fn
    monkeypatch.setitem(sys.modules, "functions_framework", ff)

    notify_dir = CLOUD_RUN / "notify_execution"
    monkeypatch.syspath_prepend(str(notify_dir))
    # Garante import fresco
    sys.modules.pop("main", None)
    mod = importlib.import_module("main")
    importlib.reload(mod)
    yield mod
    sys.modules.pop("main", None)


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def get_json(self, silent=False):
        return self._payload


def test_notify_import_succeeds_without_secrets(monkeypatch):
    """#60: o módulo deve importar mesmo sem os segredos montados (sem KeyError)."""
    for name in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    ff = types.ModuleType("functions_framework")
    ff.http = lambda fn: fn
    monkeypatch.setitem(sys.modules, "functions_framework", ff)
    monkeypatch.syspath_prepend(str(CLOUD_RUN / "notify_execution"))
    sys.modules.pop("main", None)
    importlib.import_module("main")  # não deve levantar


def test_notify_missing_secret_returns_500(notify_module, monkeypatch):
    """#60: faltando segredo, handler devolve 500 com mensagem clara (não KeyError)."""
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setenv("GMAIL_USER", "u@x.com")
    monkeypatch.setenv("NOTIFY_EMAIL", "n@x.com")
    body, code = notify_module.notify_execution(_FakeRequest({"workflow_name": "wf"}))
    assert code == 500
    assert body["status"] == "error"
    assert "GMAIL_APP_PASSWORD" in body["error"]


def test_notify_sends_email_when_configured(notify_module, monkeypatch):
    """#60: com segredos presentes, envia via SMTP e devolve (notified, 200)."""
    monkeypatch.setenv("GMAIL_USER", "u@x.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("NOTIFY_EMAIL", "n@x.com")

    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host
            sent["port"] = port
            sent["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, user, pw):
            sent["login"] = (user, pw)

        def sendmail(self, frm, to, msg):
            sent["mail"] = (frm, to, msg)

    monkeypatch.setattr(notify_module.smtplib, "SMTP_SSL", _FakeSMTP)
    body, code = notify_module.notify_execution(
        _FakeRequest({"workflow_name": "wf", "status": "SUCCESS"})
    )
    assert code == 200
    assert body["status"] == "notified"
    assert sent["timeout"] == 30  # timeout explícito (higiene)
    assert sent["login"] == ("u@x.com", "pw")
