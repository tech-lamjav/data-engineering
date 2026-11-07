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


def _rodar_cli(tmp_path, listing, vivos_texto="", *extra, indices=None):
    imagens = tmp_path / "imagens.json"
    imagens.write_text(json.dumps(listing))
    vivos = tmp_path / "vivos.txt"
    vivos.write_text(vivos_texto)
    args = [sys.executable, str(REPO_ROOT / "scripts" / "simula_cleanup_ar.py"),
            str(POLICY_SERVICOS), str(imagens), str(vivos), *extra]
    if indices is not None:
        arq = tmp_path / "indices.txt"
        arq.write_text(indices)
        args += ["--indices", str(arq)]
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


def test_cli_conta_so_os_vivos_deste_repositorio(tmp_path):
    listing = [_versao("extract-games", 1, 1)]
    proprio = f"{REPO}/extract-games@{listing[0]['version']}"
    outro = f"us-east1-docker.pkg.dev/smartbetting-dados/dbt-futebol-repo/dbt-futebol@sha256:{'e' * 64}"
    resultado = _rodar_cli(tmp_path, listing, f"{outro}\n{proprio}\n", "--esperado", "1")
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr


@pytest.mark.parametrize("texto", [
    "",                                                    # lista vazia
    "[INFO] nada aqui\n",                                  # sem digest nenhum
    "us-east1-docker.pkg.dev/smartbetting-dados/dbt-futebol-repo/dbt-futebol@sha256:" + "e" * 64,
], ids=["vazia", "sem-digest", "so-outro-repo"])
def test_cli_sem_vivo_deste_repositorio_nao_da_verde(tmp_path, texto):
    """Verde falso: com lista vazia, cortada ou de outro repo, "nenhum vivo seria apagado" é
    verdade vazia. Sai com 2 (entrada insuficiente), nunca com 0."""
    resultado = _rodar_cli(tmp_path, [_versao("a", 1, 1)], texto)
    assert resultado.returncode == 2
    assert "nenhum digest vivo DESTE repositório" in resultado.stderr


def test_cli_esperado_diferente_do_achado_sai_com_2(tmp_path):
    """Lista parcial: um serviço que falhou na leitura some da lista sem aviso."""
    listing = [_versao("a", 1, 1), _versao("b", 2, 1)]
    vivo = f"{REPO}/a@{listing[0]['version']}"
    resultado = _rodar_cli(tmp_path, listing, vivo, "--esperado", "29")
    assert resultado.returncode == 2
    assert "esperado 29" in resultado.stderr


# ---------------------------------------------------------------- índices


INDICE = "application/vnd.oci.image.index.v1+json"


def _listing_dbt_nba():
    """Forma do dbt-nba-repo em 07/11/2026: índice `live` com filho amd64 sem tag, e o
    `live-prev` (digest da última execução) filho de um índice antigo SEM tag."""
    velhos = [_versao("dbt-nba", 100 + n, 60 + n) for n in range(12)]   # empurram p/ fora das 12
    idx_live = _versao("dbt-nba", 1, 80, tags=["live", "latest"])
    idx_live["metadata"]["mediaType"] = INDICE
    filho_live = _versao("dbt-nba", 2, 80)
    idx_velho = _versao("dbt-nba", 3, 170)
    idx_velho["metadata"]["mediaType"] = INDICE
    filho_prev = _versao("dbt-nba", 4, 170, tags=["live-prev"])
    return [*velhos, idx_live, filho_live, idx_velho, filho_prev]


def _d(v):
    return f"{v['package']}@{v['version']}"


def test_listing_com_indice_sem_indices_sai_com_2(tmp_path):
    listing = _listing_dbt_nba()
    resultado = _rodar_cli(tmp_path, listing, _d(listing[12]))
    assert resultado.returncode == 2
    assert "faltou --indices" in resultado.stderr


def test_filho_sem_tag_de_indice_live_fica_e_vivo_filho_de_pai_apagado_e_risco(tmp_path):
    listing = _listing_dbt_nba()
    idx_live, filho_live, idx_velho, filho_prev = listing[12:]
    indices = (f"indice {_d(idx_live)} {_d(filho_live)}\n"
               f"indice {_d(idx_velho)} {_d(filho_prev)}\n")
    vivos = f"{_d(filho_live)}\n{_d(filho_prev)}\n"

    resultado = _rodar_cli(tmp_path, listing, vivos, indices=indices)

    # O filho amd64 sem tag do índice live fica (regra do AR); o live-prev fica pela tag,
    # mas o pai dele seria apagado e a cascata é ambígua: risco, exit 1.
    assert resultado.returncode == 1, resultado.stdout + resultado.stderr
    assert _d(filho_live) not in resultado.stdout
    assert "filho de índice que seria APAGADO" in resultado.stdout

    # Com o pai tagueado (live-prev-idx), fica verde.
    idx_velho["tags"] = ["live-prev-idx"]
    resultado = _rodar_cli(tmp_path, listing, vivos, indices=indices)
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr


def test_simular_propaga_o_keep_do_pai_para_o_filho():
    listing = _listing_dbt_nba()
    idx_live, filho_live = listing[12], listing[13]
    versoes = sim.normalizar_versoes(listing)
    pais = sim.ler_indices(f"indice {_d(idx_live)} {_d(filho_live)}\n")
    sem = sim.simular(json.loads(POLICY_DBT.read_text()), versoes, AGORA)
    com = sim.simular(json.loads(POLICY_DBT.read_text()), versoes, AGORA, pais)
    chave = (filho_live["package"], filho_live["version"])
    assert sem[chave][0] == "apaga"
    assert com[chave][0] == "mantem"


def test_linha_de_indice_malformada_e_erro():
    with pytest.raises(ValueError, match="malformada"):
        sim.ler_indices("indice so-um-digest\n")


def test_indice_sem_tamanho_nao_quebra_e_e_sinalizado(tmp_path):
    """Listing real dos repos dbt (07/11/2026): índices de attestation vêm com
    `imageSizeBytes: "None"` como string."""
    listing = [_versao("dbt", 1, 1)]
    listing[0]["metadata"] = {"mediaType": INDICE, "imageSizeBytes": "None"}
    resultado = _rodar_cli(tmp_path, listing, _d(listing[0]) + "\n", indices="")
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "é um ÍNDICE" in resultado.stdout


# ---------------------------------------------------------------- scripts/indices_ar.py

import indices_ar  # noqa: E402


def test_indices_ar_monta_url_e_linhas():
    pkg = "us-east1-docker.pkg.dev/smartbetting-dados/dbt-nba-repo/dbt-nba"
    assert indices_ar.url_manifesto(pkg, "sha256:" + "a" * 64) == (
        "https://us-east1-docker.pkg.dev/v2/smartbetting-dados/dbt-nba-repo/dbt-nba/manifests/sha256:"
        + "a" * 64)
    manifesto = {"mediaType": INDICE, "manifests": [{"digest": "sha256:" + "b" * 64},
                                                    {"digest": "sha256:" + "c" * 64}]}
    linhas = indices_ar.linhas_indice(pkg, "sha256:" + "a" * 64, manifesto)
    assert linhas == [f"indice {pkg}@sha256:{'a' * 64} {pkg}@sha256:{'b' * 64}",
                      f"indice {pkg}@sha256:{'a' * 64} {pkg}@sha256:{'c' * 64}"]
    # A saída é exatamente o que o simulador lê.
    assert len(sim.ler_indices("\n".join(linhas))[(pkg, "sha256:" + "b" * 64)]) == 1


@pytest.mark.parametrize("manifesto", [
    {"mediaType": "application/vnd.oci.image.manifest.v1+json"},
    {"mediaType": INDICE, "manifests": []},
    {"mediaType": INDICE, "manifests": [{"digest": "sha256:curto"}]},
], ids=["nao-indice", "sem-filhos", "digest-invalido"])
def test_indices_ar_falha_fechado(manifesto):
    with pytest.raises(ValueError):
        indices_ar.linhas_indice("h/p/r/i", "sha256:" + "a" * 64, manifesto)
