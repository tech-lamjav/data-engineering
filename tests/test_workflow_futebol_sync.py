"""Contrato do `workflow_futebol_sync.yml` com a trava do sync (DE#107).

O YAML não executa sem deploy, então o que dá para provar aqui é estrutural: os timeouts e,
principalmente, que 409 ("já em andamento") é tratado como aviso e NUNCA entra em
`failed_services` nem vira PARTIAL_FAILURE. A execução real só se confere depois do deploy
do workflow (runbook no PR).
"""
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / "workflow_futebol_sync.yml"

# http.get do Workflows aceita no máximo 1800 s.
TIMEOUT_HTTP_MAXIMO_WORKFLOWS = 1800


def _carrega():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _passos(doc):
    return {
        nome: corpo
        for item in doc["main"]["steps"]
        for nome, corpo in item.items()
    }


def _blocos():
    """(ambiente, bloco do passo) para sync_prd e sync_dev."""
    p = _passos(_carrega())
    return [("prd", p["sync_prd"]), ("dev", p["sync_dev"])]


def _chamada(bloco):
    for item in bloco["try"]["steps"]:
        for nome, corpo in item.items():
            if nome.startswith("call_sync_"):
                return corpo
    raise AssertionError("sem call_sync_*")


def _achata(no):
    """Todos os textos de uma subárvore do YAML (para procurar referências)."""
    if isinstance(no, dict):
        for k, v in no.items():
            yield str(k)
            yield from _achata(v)
    elif isinstance(no, list):
        for v in no:
            yield from _achata(v)
    else:
        yield str(no)


def test_http_get_do_sync_usa_o_timeout_maximo_do_workflows():
    for env, bloco in _blocos():
        chamada = _chamada(bloco)
        assert chamada["try"]["args"]["timeout"] == TIMEOUT_HTTP_MAXIMO_WORKFLOWS, env


def test_o_retry_continua_o_padrao_que_nao_repete_409():
    for env, bloco in _blocos():
        retry = _chamada(bloco)["retry"]
        assert retry["predicate"] == "${http.default_retry_predicate}", env


def _ramo_409(bloco):
    """O ramo do switch do `except` cuja condição olha o código 409."""
    for item in bloco["except"]["steps"]:
        for _, corpo in item.items():
            for ramo in corpo.get("switch", []):
                if "409" in str(ramo.get("condition", "")):
                    return ramo
    raise AssertionError("o except não tem ramo para 409")


def test_409_vira_warning_em_andamento_e_nao_entra_em_failed_services():
    for env, bloco in _blocos():
        ramo = _ramo_409(bloco)
        textos = " ".join(_achata(ramo["steps"]))
        assert "WARNING" in textos, env
        assert "em andamento" in textos, env
        assert "failed_services" not in textos, env
        assert "PARTIAL_FAILURE" not in textos, env


def test_outros_erros_continuam_virando_falha():
    for env, bloco in _blocos():
        ramos = [
            r
            for item in bloco["except"]["steps"]
            for _, corpo in item.items()
            for r in corpo.get("switch", [])
            if "409" not in str(r.get("condition", ""))
        ]
        assert len(ramos) == 1, env
        textos = " ".join(_achata(ramos[0]["steps"]))
        assert "PARTIAL_FAILURE" in textos and "failed_services" in textos, env
        assert f"sync-bq-to-postgres[futebol/{env}]" in textos, env


def test_a_condicao_do_409_nao_quebra_com_erro_sem_campo_code():
    """TimeoutError/ConnectionError não têm `code`; o Workflows levanta em chave ausente."""
    for env, bloco in _blocos():
        condicao = str(_ramo_409(bloco)["condition"])
        assert 'map.get(e, "code")' in condicao, env
