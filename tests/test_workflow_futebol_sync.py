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


# ------------------------------------------------------------------
# DE#108: fallback, falha de troca e aviso de sombra órfã chegam ao log de conclusão, de onde o
# resumo diário os lê (histórias 9, 20, 27, 54). Antes só viviam no corpo HTTP, que o workflow
# descartava, e a falha de troca aparecia como o serviço inteiro, sem o nome da tabela.
# ------------------------------------------------------------------
CAMPOS_DO_RESUMO = ("summary", "falhas", "avisos")


def _ramo_de_erro_generico(bloco):
    ramos = [
        r
        for item in bloco["except"]["steps"]
        for _, corpo in item.items()
        for r in corpo.get("switch", [])
        if "409" not in str(r.get("condition", ""))
    ]
    assert len(ramos) == 1
    return ramos[0]


def test_o_resumo_de_cada_ambiente_nasce_nulo_para_o_campo_ser_aditivo():
    """Um 409 ou erro sem corpo legível deixa o resumo sem escrita; o log_completion lê a
    variável e, sem o init, derrubaria o workflow inteiro."""
    atrib = _init()
    for env in ("prd", "dev"):
        assert f"resumo_{env}" in atrib and atrib[f"resumo_{env}"] is None, env


def test_os_dois_ambientes_guardam_o_resultado_da_chamada():
    for env, bloco in _blocos():
        assert _chamada(bloco)["try"].get("result") == f"sync_{env}_result", env


def test_no_sucesso_o_resumo_sai_do_corpo_da_resposta_so_com_map_get():
    """Imagem velha (corpo sem os campos) tem de dar nulo, não erro: acesso direto a chave
    ausente derruba o workflow."""
    for env, bloco in _blocos():
        atrib = _atribuicoes(bloco["try"]["steps"])
        resumo = atrib[f"resumo_{env}"]
        assert set(resumo) == set(CAMPOS_DO_RESUMO), env
        for campo in CAMPOS_DO_RESUMO:
            expr = str(resumo[campo])
            assert "map.get" in expr and f"sync_{env}_result.body" in expr and campo in expr, (env, campo)


def _passo_que_guarda_o_resumo_no_erro(bloco):
    for item in _ramo_de_erro_generico(bloco)["steps"]:
        for nome, corpo in item.items():
            if "try" in corpo:
                return nome, corpo
    raise AssertionError("o ramo de erro não lê o corpo da resposta")


def test_no_erro_o_corpo_e_lido_dentro_de_um_try_proprio_para_nao_derrubar_o_workflow():
    """Corpo de erro que não é JSON (HTML de um 502 do Cloud Run) faz map.get sobre string
    levantar: sem o try aninhado, o except que marca PARTIAL_FAILURE morreria no meio."""
    for env, bloco in _blocos():
        _, passo = _passo_que_guarda_o_resumo_no_erro(bloco)
        assert "except" in passo, env
        lido = _atribuicoes(passo["try"]["steps"])[f"resumo_{env}"]
        assert set(lido) == set(CAMPOS_DO_RESUMO), env
        for campo in CAMPOS_DO_RESUMO:
            expr = str(lido[campo])
            assert 'map.get(e, ["body", "%s"])' % campo in expr, (env, campo)


def test_a_leitura_do_corpo_vem_depois_de_marcar_a_falha_e_nao_a_substitui():
    for env, bloco in _blocos():
        nomes = [n for item in _ramo_de_erro_generico(bloco)["steps"] for n in item]
        guarda = _passo_que_guarda_o_resumo_no_erro(bloco)[0]
        marca = next(n for n in nomes if n.startswith("handle_sync_"))
        assert nomes.index(marca) < nomes.index(guarda), env
        textos = " ".join(_achata(_ramo_de_erro_generico(bloco)["steps"]))
        assert "PARTIAL_FAILURE" in textos and f"sync-bq-to-postgres[futebol/{env}]" in textos


def test_o_log_de_erro_do_sync_leva_o_corpo_da_resposta():
    """O 500 de falha de troca traz `falhas` com os nomes das tabelas: precisa estar no log."""
    for env, bloco in _blocos():
        log = next(
            corpo
            for item in _ramo_de_erro_generico(bloco)["steps"]
            for nome, corpo in item.items()
            if nome == f"log_sync_{env}_error"
        )
        assert 'map.get(e, "body")' in " ".join(_achata(log)), env


def test_o_ramo_409_nao_escreve_o_resumo():
    for env, bloco in _blocos():
        assert f"resumo_{env}" not in " ".join(_achata(_ramo_409(bloco))), env


def test_o_log_de_conclusao_emite_o_resumo_dos_dois_ambientes():
    dados = _log_completion()
    assert dados["sync_prd"] == "${resumo_prd}"
    assert dados["sync_dev"] == "${resumo_dev}"
    # nada do que o resumo diário já lê mudou
    for campo in ("status", "duration_seconds", "failed_services", "failed_count", "dev_size_mb"):
        assert campo in dados


def test_o_passe_prd_continua_sem_tocar_no_tamanho_do_dev():
    assert "dev_size_mb" not in " ".join(_achata(dict(_blocos())["prd"]))


# ------------------------------------------------------------------
# DE#109: cache de serving das odds em PRD, ligado pelo workflow (lançamento escuro)
# ------------------------------------------------------------------
def test_o_cache_de_serving_nasce_como_variavel_do_init_so_para_prd():
    atrib = _init()
    assert "cache_serving_prd" in atrib
    assert "cache_serving_dev" not in atrib  # em DEV o filtro de mercados já vale sempre


def test_prd_passa_o_cache_de_serving_ao_servico_e_dev_nao():
    blocos = dict(_blocos())
    prd = _chamada(blocos["prd"])["try"]["args"]["query"]
    dev = _chamada(blocos["dev"])["try"]["args"]["query"]
    assert prd["cache_serving"] == "${cache_serving_prd}"
    assert "cache_serving" not in dev  # o serviço recusa o parâmetro em DEV


def test_neste_commit_o_cache_de_serving_esta_desligado():
    """A imagem entra com o cache DESLIGADO (lançamento escuro): ligar é o último passo do
    runbook, depois de IAM, smoke, snapshot congelado e deploy. Este teste muda junto com o PR
    que ligar."""
    assert _init()["cache_serving_prd"] == ""


def test_as_odds_na_troca_de_prd_exigem_o_cache_de_serving_ligado_no_mesmo_yaml():
    """Guarda que sobrevive ao liga: o serviço recusa (ValueError) odds na troca de PRD sem o
    filtro, e uma recusa só apareceria no primeiro run. Aqui ela aparece no CI."""
    from src.sync.odds_serving import TABELA_ODDS
    from src.sync.troca import parse_lista

    atrib = _init()
    na_troca = parse_lista(atrib["troca_prd"]) | parse_lista(atrib["staged_prd"])
    if TABELA_ODDS in na_troca:
        assert TABELA_ODDS in parse_lista(atrib["cache_serving_prd"])
