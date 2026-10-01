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


# ------------------------------------------------------------------
# DE#106: o tamanho do DEV (campo aditivo) chega ao log de conclusão
# ------------------------------------------------------------------
def _atribuicoes(passos_de_um_bloco):
    """{variável: expressão} de todos os `assign` de uma lista de passos (um nível)."""
    out = {}
    for item in passos_de_um_bloco:
        for _, corpo in item.items():
            for atrib in corpo.get("assign", []):
                out.update(atrib)
    return out


def _log_completion():
    return _passos(_carrega())["log_completion"]["args"]["data"]


def test_dev_size_mb_nasce_nulo_para_o_campo_ser_aditivo():
    """Sem o init, um 409/falha no DEV deixaria `dev_size_mb` indefinido e o log_completion
    (que lê a variável) derrubaria o workflow inteiro."""
    init = _passos(_carrega())["init"]["assign"]
    atrib = {k: v for a in init for k, v in a.items()}

    assert "dev_size_mb" in atrib and atrib["dev_size_mb"] is None


def test_o_passe_dev_guarda_o_resultado_da_chamada():
    chamada = _chamada(dict(_blocos())["dev"])

    assert chamada["try"].get("result"), "sem `result:` o corpo da resposta se perde"


def test_o_tamanho_so_e_lido_no_caminho_de_sucesso_do_dev_e_com_map_get():
    """Resposta de imagem velha (sem o campo) ou corpo ausente não pode quebrar o passo."""
    bloco = dict(_blocos())["dev"]
    guardado = _chamada(bloco)["try"]["result"]
    sucesso = _atribuicoes(bloco["try"]["steps"])

    expressao = str(sucesso["dev_size_mb"])
    assert "map.get" in expressao and "dev_size_mb" in expressao and guardado in expressao
    # o ramo de erro (409 ou falha) nunca escreve o campo: ele fica nulo
    assert "dev_size_mb" not in " ".join(_achata(bloco["except"]))


def test_o_passe_prd_nao_toca_no_tamanho_do_dev():
    bloco = dict(_blocos())["prd"]

    assert "dev_size_mb" not in " ".join(_achata(bloco))


def test_o_log_de_conclusao_emite_o_tamanho_do_dev():
    dados = _log_completion()

    assert dados["dev_size_mb"] == "${dev_size_mb}"
    # os campos que o resumo diário já lê não mudaram
    assert dados["workflow_name"] == "workflow_futebol_sync"
    for campo in ("status", "duration_seconds", "failed_services", "failed_count"):
        assert campo in dados


# ------------------------------------------------------------------
# DE#108: carga por troca ligada por tabela, no workflow (lançamento escuro)
# ------------------------------------------------------------------
def _init():
    init = _passos(_carrega())["init"]["assign"]
    return {k: v for a in init for k, v in a.items()}


def test_a_selecao_da_troca_nasce_como_variavel_do_init_para_cada_ambiente():
    atrib = _init()
    for nome in ("troca_prd", "staged_prd", "troca_dev", "staged_dev"):
        assert nome in atrib, nome


def test_cada_ambiente_passa_a_propria_selecao_ao_servico():
    blocos = dict(_blocos())
    for env in ("prd", "dev"):
        query = _chamada(blocos[env])["try"]["args"]["query"]
        assert query["troca"] == f"${{troca_{env}}}", env
        assert query["staged"] == f"${{staged_{env}}}", env


def test_neste_commit_a_troca_esta_desligada_em_prd_e_em_dev():
    """A imagem entra com a troca DESLIGADA (ADR 0005): ligar é uma edição deliberada, tabela a
    tabela, com confirmação do dono. Este teste muda junto com o PR que ligar a primeira."""
    atrib = _init()
    for nome in ("troca_prd", "staged_prd", "troca_dev", "staged_dev"):
        assert atrib[nome] == "", nome


def test_a_selecao_commitada_respeita_o_desenho_mesmo_depois_de_ligada():
    """Guarda que sobrevive ao liga: odds fora; tabela com view dependente só no staged; troca
    só em tabela do alvo do sync."""
    from src.sync.alvo import resolve_alvo_sync
    from src.sync.retencao import TABELAS_RETENCAO_PRODUTO_FUTEBOL
    from src.sync.troca import TABELAS_FORA_DA_TROCA, parse_lista

    _, _, alvo = resolve_alvo_sync("futebol")
    premissas = {t for t in TABELAS_RETENCAO_PRODUTO_FUTEBOL if t.startswith("int_futebol_premissas_")}
    atrib = _init()
    for amb in ("prd", "dev"):
        troca_sel, staged_sel = parse_lista(atrib[f"troca_{amb}"]), parse_lista(atrib[f"staged_{amb}"])
        assert not ((troca_sel | staged_sel) & TABELAS_FORA_DA_TROCA), amb
        assert (troca_sel | staged_sel) <= set(alvo), amb
        assert not (troca_sel & premissas), f"{amb}: premissas têm view dependente, vão no staged"
