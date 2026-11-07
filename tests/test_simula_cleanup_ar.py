"""Guarda do simulador local da cleanup policy do Artifact Registry
(`scripts/simula_cleanup_ar.py`) e das policies versionadas em `infra/`.

O dry-run do próprio Artifact Registry só aparece no audit log de Data Write, desligado no
projeto. A simulação local é o único jeito de ver o efeito antes de aplicar; se ela divergir
da semântica real, a policy aplicada apaga o que a simulação disse que mantinha.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import simula_cleanup_ar as sim  # noqa: E402

POLICY_SERVICOS = REPO_ROOT / "infra" / "ar-cleanup-cloud-run-source-deploy.json"
POLICY_DBT = REPO_ROOT / "infra" / "ar-cleanup-dbt.json"
REPO = "us-east1-docker.pkg.dev/smartbetting-dados/cloud-run-source-deploy"
AGORA = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def _versao(pacote, n, dias, tags=None, pkg_repo=REPO):
    return {
        "package": f"{pkg_repo}/{pacote}",
        "version": "sha256:" + f"{n:064x}",
        "tags": tags or [],
        "createTime": (AGORA - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "metadata": {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                     "imageSizeBytes": str(127 * 2**20)},
    }


def _simular(policy_path, listing):
    versoes = sim.normalizar_versoes(listing)
    return versoes, sim.simular(json.loads(policy_path.read_text()), versoes, AGORA)


def test_policies_versionadas_sao_validas():
    for caminho in (POLICY_SERVICOS, POLICY_DBT):
        sim.validar_policies(json.loads(caminho.read_text()))


def test_policy_dos_servicos_tem_os_numeros_combinados():
    """Keep `live*`, keep 12 recentes (cada deploy --source gera ~4 versões: 3 deploys de
    rollback), delete > 30 dias com QUALQUER tag-state (com `untagged`, toda versão que um
    dia teve `latest` ficaria para sempre)."""
    p = {x["name"]: x for x in json.loads(POLICY_SERVICOS.read_text())}
    assert p["keep-live"]["condition"] == {"tagState": "tagged", "tagPrefixes": ["live"]}
    assert p["keep-12-recentes"]["mostRecentVersions"] == {"keepCount": 12}
    assert p["delete-mais-de-30d"]["condition"] == {"tagState": "any", "olderThan": "30d"}


def test_policy_dbt_mantem_live_e_latest_e_9_recentes():
    p = {x["name"]: x for x in json.loads(POLICY_DBT.read_text())}
    assert p["keep-live-e-latest"]["condition"]["tagPrefixes"] == ["live", "latest"]
    assert p["keep-9-recentes"]["mostRecentVersions"] == {"keepCount": 9}
    assert p["delete-mais-de-30d"]["condition"] == {"tagState": "any", "olderThan": "30d"}


def test_live_velho_sobrevive_e_o_resto_velho_cai():
    # 20 versões de 40..59 dias; a mais velha de todas tem `live` (serviço nunca mais
    # deployado). As 12 mais novas ficam pelo keepCount; a live fica pela tag.
    listing = [_versao("extract-games", n, 40 + n) for n in range(20)]
    listing[19]["tags"] = ["live"]
    versoes, r = _simular(POLICY_SERVICOS, listing)

    decisoes = [r[(f"{REPO}/extract-games", v["version"])][0] for v in listing]
    assert decisoes[:12] == ["mantem"] * 12
    assert decisoes[12:19] == ["apaga"] * 7
    assert decisoes[19] == "mantem"


def test_keep_count_e_por_pacote():
    listing = ([_versao("a", n, 100 + n) for n in range(13)]
               + [_versao("b", 100 + n, 100 + n) for n in range(5)])
    _, r = _simular(POLICY_SERVICOS, listing)
    apagadas = [k for k, (d, _) in r.items() if d == "apaga"]
    assert len(apagadas) == 1  # só a 13ª de "a"; as 5 de "b" ficam
    assert apagadas[0][0].endswith("/a")


def test_versao_recente_nunca_cai_mesmo_fora_do_keep_count():
    listing = [_versao("a", n, 1) for n in range(20)]
    _, r = _simular(POLICY_SERVICOS, listing)
    assert all(d == "mantem" for d, _ in r.values())


def test_latest_nao_protege_nos_servicos_mas_protege_no_dbt():
    """Nos serviços, `latest` é movida no push, antes da revisão existir: não é proteção.
    Nos jobs dbt ela continua na lista de Keep (o build-and-push só move `latest` junto)."""
    dbt_repo = "us-east1-docker.pkg.dev/smartbetting-dados/dbt-futebol-repo"
    # 13 versões de 40+ dias: a mais velha fica fora das 12 recentes.
    listing = [_versao("x", n, 40 + n) for n in range(13)]
    listing[12]["tags"] = ["latest"]
    _, r = _simular(POLICY_SERVICOS, listing)
    assert r[(f"{REPO}/x", listing[12]["version"])][0] == "apaga"

    listing_dbt = [_versao("dbt-futebol", n, 40 + n, pkg_repo=dbt_repo) for n in range(13)]
    listing_dbt[12]["tags"] = ["latest"]
    _, r = _simular(POLICY_DBT, listing_dbt)
    assert r[(f"{dbt_repo}/dbt-futebol", listing_dbt[12]["version"])][0] == "mantem"
    assert r[(f"{dbt_repo}/dbt-futebol", listing_dbt[11]["version"])][0] == "apaga"  # 10ª+


def test_tags_como_string_separada_por_virgula():
    listing = [_versao("a", n, 100 + n) for n in range(14)]
    listing[13]["tags"] = "latest, live-prev"
    _, r = _simular(POLICY_SERVICOS, listing)
    assert r[(f"{REPO}/a", listing[13]["version"])][0] == "mantem"


@pytest.mark.parametrize("policy, trecho", [
    ([{"name": "x", "action": {"type": "Delete"}, "condition": {"olderThen": "30d"}}],
     "campos desconhecidos"),
    ([{"name": "x", "action": {"type": "Keep"},
       "condition": {"tagState": "any", "tagPrefixes": ["live"]}}], "exige tagState tagged"),
    ([{"name": "x", "action": {"type": "Delete"}, "condition": {"olderThan": "30 dias"}}],
     "duração"),
    ([{"name": "x", "action": {"type": "Delete"}, "mostRecentVersions": {"keepCount": 3}}],
     "só existe em Keep"),
])
def test_policy_desconhecida_ou_errada_e_erro_e_nao_ignorada(policy, trecho):
    with pytest.raises(sim.PolicyInvalida, match=trecho):
        sim.validar_policies(policy)


def _rodar_cli(tmp_path, listing, vivos_texto=None):
    imagens = tmp_path / "imagens.json"
    imagens.write_text(json.dumps(listing))
    args = [sys.executable, str(REPO_ROOT / "scripts" / "simula_cleanup_ar.py"),
            str(POLICY_SERVICOS), str(imagens)]
    if vivos_texto is not None:
        vivos = tmp_path / "vivos.txt"
        vivos.write_text(vivos_texto)
        args.append(str(vivos))
    return subprocess.run(args, capture_output=True, text=True)


def test_cli_falha_quando_um_vivo_sem_tag_seria_apagado(tmp_path):
    """É o caso "aplicaram a policy antes do backfill da tag live": o serviço serve uma
    versão velha, fora das 12 mais novas, ainda sem tag."""
    listing = [_versao("extract-games", n, 40 + n) for n in range(15)]
    vivo = f"{REPO}/extract-games@{listing[14]['version']}"
    # A saída do `deploy_cloud_run.sh --lista-vivos` inteira, com o ruído de log.
    texto = f"[INFO] Tag live: extract-games\nvivo extract-games live {vivo}\n"

    resultado = _rodar_cli(tmp_path, listing, texto)
    assert resultado.returncode == 1, resultado.stdout
    assert "INSEGURO" in resultado.stdout
    assert "seria APAGADO" in resultado.stdout

    listing[14]["tags"] = ["live"]
    resultado = _rodar_cli(tmp_path, listing, texto)
    assert resultado.returncode == 0, resultado.stdout
    assert "Nenhum digest vivo seria apagado" in resultado.stdout


def test_cli_falha_quando_o_vivo_nao_esta_no_listing(tmp_path):
    listing = [_versao("extract-games", 1, 1)]
    vivo = f"{REPO}/extract-games@sha256:{'f' * 64}"
    resultado = _rodar_cli(tmp_path, listing, f"vivo extract-games live {vivo}\n")
    assert resultado.returncode == 1
    assert "NÃO está no listing" in resultado.stdout


def test_cli_ignora_vivo_de_outro_repositorio(tmp_path):
    listing = [_versao("extract-games", 1, 1)]
    outro = f"us-east1-docker.pkg.dev/smartbetting-dados/dbt-futebol-repo/dbt-futebol@sha256:{'e' * 64}"
    resultado = _rodar_cli(tmp_path, listing, f"{outro}\n")
    assert resultado.returncode == 0, resultado.stdout


def test_cli_sem_vivos_avisa_que_nao_conferiu_producao(tmp_path):
    resultado = _rodar_cli(tmp_path, [_versao("a", 1, 1)])
    assert resultado.returncode == 0
    assert "Sem lista de vivos" in resultado.stdout


def test_indice_sem_tamanho_nao_quebra_e_e_sinalizado(tmp_path):
    """Listing real dos repos dbt (07/11/2026): índices de attestation vêm com
    `imageSizeBytes: "None"` como string."""
    listing = [_versao("dbt", 1, 1)]
    listing[0]["metadata"] = {"mediaType": "application/vnd.oci.image.index.v1+json",
                              "imageSizeBytes": "None"}
    vivo = f"{REPO}/dbt@{listing[0]['version']}"
    resultado = _rodar_cli(tmp_path, listing, vivo + "\n")
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "é um ÍNDICE" in resultado.stdout
