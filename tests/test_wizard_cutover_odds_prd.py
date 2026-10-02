"""O wizard de cutover das odds em PRD (DE#109): estrutura e ORDEM dos passos humanos.

O wizard abre navegador, pede reautenticação e muda IAM, deploy e DDL reais, então NÃO roda em
teste. O que dá para provar é estático: a sintaxe do bash, que `TOTAL_STAGES` bate com os estágios
escritos, e que os passos que só um humano dá estão na ordem que o dono fixou (reauth do gcloud,
snapshot, SQL administrativo, conta dedicada e papel, smoke, nenhuma execução ACTIVE, workflow ANTES
da imagem, ligar o cache, conferir pelo dado, ligar a troca).
"""
import re
import subprocess
from pathlib import Path

WIZARD = Path(__file__).resolve().parent.parent / "scripts" / "wizard_cutover_odds_prd.sh"


def _texto():
    return WIZARD.read_text(encoding="utf-8")


def test_a_sintaxe_do_bash_e_valida():
    resultado = subprocess.run(["bash", "-n", str(WIZARD)], capture_output=True, text=True)
    assert resultado.returncode == 0, resultado.stderr


def test_o_total_de_estagios_bate_com_os_estagios_escritos():
    t = _texto()
    escritos = len(re.findall(r'^stage "', t, flags=re.MULTILINE))
    assert re.search(rf"^TOTAL_STAGES={escritos}$", t, flags=re.MULTILINE), escritos


def test_os_passos_humanos_estao_na_ordem_do_dono():
    t = _texto()
    ordem = [
        'stage "Pré-condições',
        "gcloud auth login",
        'stage "Snapshot congelado',
        "scripts/snapshot_odds_pre_corte.py --apply",
        'stage "SQL administrativo',
        "gcloud iam service-accounts create",
        "roles/bigquery.jobUser",
        "roles/iam.serviceAccountTokenCreator",
        "scripts/smoke_iam_sync_dev.py",
        "state=ACTIVE",
        "scripts/deploy_workflows.sh workflow-futebol-sync",  # o YAML ANTES da imagem
        "scripts/deploy_cloud_run.sh sync-bq-to-postgres",
        'stage "LIGAR o cache de serving',
        'stage "Conferir pelo DADO',
        'stage "LIGAR a carga por troca nas odds',
    ]
    posicoes = []
    for trecho in ordem:
        assert trecho in t, trecho
        posicoes.append(t.index(trecho))
    assert posicoes == sorted(posicoes), "passos fora de ordem"


def test_a_imagem_do_sync_vai_com_a_conta_dedicada_e_so_ela():
    t = _texto()
    assert 'env SYNC_SERVICE_ACCOUNT="$SA_NOME" scripts/deploy_cloud_run.sh sync-bq-to-postgres' in t
    # nenhum deploy completo (sem argumento): derivaria os 29 serviços de um checkout qualquer
    assert not re.search(r"deploy_cloud_run\.sh\s*(?:$|\n|\")", t)


def _comandos():
    """Cada comando do wizard como UMA linha (continuações `\\` unidas), sem comentários."""
    juntos, atual = [], ""
    for linha in _texto().splitlines():
        if atual:
            atual += " " + linha.strip()
        else:
            atual = linha.strip()
        if atual.endswith("\\"):
            atual = atual[:-1].rstrip()
            continue
        juntos.append(atual)
        atual = ""
    return [c for c in juntos if c and not c.startswith("#")]


def test_todo_comando_que_muda_algo_passa_por_confirmacao():
    """Os comandos de escrita só rodam via `so_se_confirmar` (o resto é leitura ou texto)."""
    perigosos = (
        "add-iam-policy-binding", "service-accounts create", "deploy_workflows.sh",
        "deploy_cloud_run.sh", "snapshot_odds_pre_corte.py --apply", "aplica_sql_admin",
        "gcloud auth login", "application-default login",
    )
    achados = 0
    for comando in _comandos():
        if comando.startswith(("say ", "step ", "note ", "warn ")):
            continue
        for p in perigosos:
            if p in comando:
                achados += 1
                assert comando.startswith("so_se_confirmar") or comando.startswith("aplica_sql_admin()"), comando
    assert achados >= 8  # o teste não é vacuamente verde


def test_a_checagem_de_execucao_active_se_repete_colada_ao_redeploy_do_servico():
    """O sync roda de hora em hora: a checagem do estágio 8 envelhece durante o deploy do workflow.

    A mesma checagem (a função `confere_sem_execucao_active`) roda de novo DENTRO do estágio do
    deploy do serviço, depois do deploy do workflow e antes do `deploy_cloud_run.sh`.
    """
    t = _texto()
    chamadas = [m.start() for m in re.finditer(r"^confere_sem_execucao_active$", t, flags=re.MULTILINE)]
    deploy_workflow = t.index("scripts/deploy_workflows.sh workflow-futebol-sync")
    deploy_servico = t.index("scripts/deploy_cloud_run.sh sync-bq-to-postgres")
    antes_do_workflow = [p for p in chamadas if p < deploy_workflow]
    coladas_ao_servico = [p for p in chamadas if deploy_workflow < p < deploy_servico]
    assert antes_do_workflow, "falta a checagem do estágio 8"
    assert coladas_ao_servico, "falta repetir a checagem logo antes do redeploy do serviço"
