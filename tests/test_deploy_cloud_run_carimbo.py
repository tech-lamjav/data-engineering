"""Guarda da leitura de conferência do carimbo (DE #50).

`test_deploy_cloud_run_argv.py` já prova o caso feliz (o stub ecoa de volta o que o
`--set-env-vars` mandou, e o deploy passa). Este arquivo prova o caso que a issue pede
como critério de aceite: **o deploy FALHA** quando `PROCEDENCIA_HASH` não volta na
leitura pós-deploy — carimbo que o próprio deploy não confirma é carimbo que some na
primeira refatoração (decisão 6 do ADR 0001).
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_deploy_cloud_run_argv as base  # reusa _build_fake_repo, GCLOUD_STUB etc.


def _run_extract_games(tmp_path, extra_env=None):
    root = tmp_path / "repo"
    root.mkdir()
    base._build_fake_repo(root, api_football_key=False)

    stub_dir = root / "_stubs"
    stub_dir.mkdir()
    (stub_dir / "gcloud").write_text(base.GCLOUD_STUB, encoding="utf-8")
    (stub_dir / "gcloud").chmod(0o755)
    (stub_dir / "sleep").write_text(base.SLEEP_STUB, encoding="utf-8")
    (stub_dir / "sleep").chmod(0o755)

    capture_dir = root / "_argv"
    capture_dir.mkdir()

    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "CAPTURE_DIR": str(capture_dir),
        "HOME": str(root),
        **(extra_env or {}),
    }
    resultado = subprocess.run(
        ["bash", str(root / "scripts" / "deploy_cloud_run.sh"), "extract-games"],
        capture_output=True, text=True, env=env, cwd=str(root),
    )
    tags_log = capture_dir / "tags.log"
    tags = tags_log.read_text(encoding="utf-8").splitlines() if tags_log.exists() else []
    return resultado, tags


def test_deploy_falha_quando_carimbo_nao_volta_na_leitura(tmp_path):
    # STUB_ENV_VAZIO: a leitura pós-deploy devolve um container SEM `env` nenhum —
    # simula um serviço cujo `--set-env-vars` não pegou (ou foi limpo por outro processo
    # entre o deploy e a leitura), independente do que o `run deploy` recebeu.
    resultado, tags = _run_extract_games(tmp_path, {"STUB_ENV_VAZIO": "1"})

    assert resultado.returncode != 0, (
        "deploy_cloud_run.sh precisa sair com erro quando o carimbo nao volta na "
        "leitura de conferencia pos-deploy\n" + resultado.stdout
    )
    assert "não voltou na leitura de conferência" in resultado.stdout
    assert "esperado:" in resultado.stdout
    assert "<ausente>" in resultado.stdout


def test_tag_live_e_aplicada_mesmo_quando_o_carimbo_nao_confere(tmp_path):
    """O deploy já subiu e o tráfego já está no digest novo quando o carimbo é conferido.
    A tag `live` vem ANTES e independente do carimbo: se viesse depois do `return 1`, o
    digest vivo ficaria sem proteção contra a cleanup policy do Artifact Registry."""
    resultado, tags = _run_extract_games(tmp_path, {"STUB_ENV_VAZIO": "1"})

    assert resultado.returncode != 0
    pacote = "us-east1-docker.pkg.dev/fake-project/cloud-run-source-deploy/extract-games"
    assert len(tags) == 1, tags
    assert tags[0].startswith(f"add {pacote}@sha256:")
    assert tags[0].endswith(f" {pacote}:live --quiet")


def test_deploy_passa_quando_o_stub_ecoa_o_carimbo_de_volta(tmp_path):
    """Controle: o mesmo cenário, mas sem STUB_ENV_VAZIO (o stub ecoa de volta o
    `--set-env-vars` real) — prova que a falha acima é do carimbo ausente,
    não de algum outro efeito colateral do arranjo do teste."""
    resultado, _ = _run_extract_games(tmp_path)

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "Carimbo de procedência conferido" in resultado.stdout


if __name__ == "__main__":
    sys.exit(subprocess.run(["python3", "-m", "pytest", str(Path(__file__)), "-v"]).returncode)
