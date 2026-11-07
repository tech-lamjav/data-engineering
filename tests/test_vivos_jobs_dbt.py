"""Guarda do `scripts/vivos_jobs_dbt.sh`: os digests vivos dos jobs dbt, contra um gcloud de
mentira. Prova o fail-closed (nada de `<pacote>@` sem digest) e que o script funciona dentro de
`while read` (o gcloud de verdade consome o stdin do loop; o stub também, de propósito)."""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "vivos_jobs_dbt.sh"
PKG = "us-east1-docker.pkg.dev/smartbetting-dados/{repo}/{img}"
A, B, C = ("sha256:" + x * 64 for x in "abc")

# STUB_SPEC_<job>, STUB_EXEC_<job> (com _ no lugar de -) e STUB_LATEST dão as respostas. O stub
# lê o stdin inteiro como faria um gcloud interativo: se o script não usar </dev/null, o
# `while read` do teste perde as linhas seguintes.
GCLOUD_STUB = r"""#!/bin/bash
cat >/dev/null
job=""
for a in "$@"; do case "$a" in --job=*) job="${a#--job=}" ;; esac; done
var() { local n="$1_$(echo "$2" | tr '-' '_')"; printf '%s' "${!n:-}"; }
case "$1 $2 $3" in
    "run jobs describe") var STUB_SPEC "$4"; echo; exit 0 ;;
    "run jobs executions") var STUB_EXEC "$job"; echo; exit 0 ;;
    "artifacts docker images") echo "${STUB_LATEST:-}"; exit 0 ;;
esac
exit 1
"""


@pytest.fixture
def stub(tmp_path):
    d = tmp_path / "stubs"
    d.mkdir()
    (d / "gcloud").write_text(GCLOUD_STUB)
    (d / "gcloud").chmod(0o755)
    return d


def rodar(stub, *jobs, **env):
    return subprocess.run(
        ["bash", str(SCRIPT), *jobs], capture_output=True, text=True,
        env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", **env},
    )


def test_spec_por_tag_e_resolvido_e_execucao_diferente_vira_live_prev(stub):
    fut = PKG.format(repo="dbt-futebol-repo", img="dbt-futebol")
    r = rodar(stub, "dbt-futebol", STUB_SPEC_dbt_futebol=f"{fut}:latest", STUB_LATEST=A,
              STUB_EXEC_dbt_futebol=f"{fut}@{B}")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [f"vivo dbt-futebol live {fut}@{A}",
                                     f"vivo dbt-futebol live-prev {fut}@{B}"]


def test_spec_por_digest_e_execucao_igual_da_uma_linha(stub):
    nba = PKG.format(repo="dbt-nba-repo", img="dbt-nba")
    r = rodar(stub, "dbt-nba", STUB_SPEC_dbt_nba=f"{nba}@{C}", STUB_EXEC_dbt_nba=f"{nba}@{C}")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [f"vivo dbt-nba live {nba}@{C}"]


@pytest.mark.parametrize("latest, execucao", [
    ("", None),                       # describe da tag voltou vazio → seria "<pacote>@"
    ("sha256:curto", None),
    (A, "{pkg}:latest"),              # execução sem digest
], ids=["latest-vazio", "latest-curto", "execucao-sem-digest"])
def test_digest_ilegivel_aborta_em_vez_de_imprimir_pacote_sem_digest(stub, latest, execucao):
    fut = PKG.format(repo="dbt-futebol-repo", img="dbt-futebol")
    env = {"STUB_SPEC_dbt_futebol": f"{fut}:latest", "STUB_LATEST": latest}
    if execucao:
        env["STUB_EXEC_dbt_futebol"] = execucao.format(pkg=fut)
    r = rodar(stub, "dbt-futebol", **env)
    assert r.returncode == 1
    assert "ERRO" in r.stderr
    assert not any(l.endswith("@") for l in r.stdout.splitlines())


def test_dentro_de_while_read_processa_os_dois_jobs(stub, tmp_path):
    """Regressão de 07/11/2026: no runbook, o gcloud dentro do `while read` comeu o stdin e só o
    1º job foi tagueado. O stub consome stdin como o gcloud; o loop tem de ver os dois."""
    fut = PKG.format(repo="dbt-futebol-repo", img="dbt-futebol")
    nba = PKG.format(repo="dbt-nba-repo", img="dbt-nba")
    loop = f'printf "dbt-futebol\\ndbt-nba\\n" | while read -r job; do bash {SCRIPT} "$job"; done'
    r = subprocess.run(
        ["bash", "-c", loop], capture_output=True, text=True,
        env={"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
             "STUB_SPEC_dbt_futebol": f"{fut}@{A}", "STUB_SPEC_dbt_nba": f"{nba}@{B}"},
    )
    assert r.returncode == 0, r.stderr
    assert [l.split()[1] for l in r.stdout.splitlines()] == ["dbt-futebol", "dbt-nba"]
