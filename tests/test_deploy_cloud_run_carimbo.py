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
import test_deploy_cloud_run_argv as base  # reusa _build_fake_repo, SLEEP_STUB etc.

# Igual ao GCLOUD_STUB do golden test, exceto que `--format=json` (a leitura de
# conferência) sempre devolve um container SEM `env` nenhum — simula um serviço cujo
# `--set-env-vars` não pegou (ou foi limpo por outro processo entre o deploy e a
# leitura), independente do que o `run deploy` recebeu.
GCLOUD_STUB_SEM_CARIMBO_NA_LEITURA = r"""#!/bin/bash
if [ "$1" = "run" ] && [ "$2" = "deploy" ]; then
    for arg in "$@"; do printf '%s\n' "$arg"; done > "$CAPTURE_DIR/$3.argv"
    exit 0
fi
case "$1 $2" in
    "auth list") echo "tester@example.com"; exit 0 ;;
    "projects describe") echo "123456789"; exit 0 ;;
esac
if [ "$1" = "run" ] && [ "$2" = "services" ] && [ "$3" = "describe" ]; then
    for arg in "$@"; do
        case "$arg" in
            --format=*status.url*) echo "https://fake.run.app"; exit 0 ;;
            --format=*conditions*) echo "True"; exit 0 ;;
            --format=json)
                echo '{"spec": {"template": {"spec": {"containers": [{"env": []}]}}}}'
                exit 0
                ;;
        esac
    done
fi
exit 0
"""


def _run_extract_games(tmp_path, gcloud_stub_body):
    root = tmp_path / "repo"
    root.mkdir()
    base._build_fake_repo(root, api_football_key=False)

    stub_dir = root / "_stubs"
    stub_dir.mkdir()
    (stub_dir / "gcloud").write_text(gcloud_stub_body, encoding="utf-8")
    (stub_dir / "gcloud").chmod(0o755)
    (stub_dir / "sleep").write_text(base.SLEEP_STUB, encoding="utf-8")
    (stub_dir / "sleep").chmod(0o755)

    capture_dir = root / "_argv"
    capture_dir.mkdir()

    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "CAPTURE_DIR": str(capture_dir),
        "HOME": str(root),
    }
    return subprocess.run(
        ["bash", str(root / "scripts" / "deploy_cloud_run.sh"), "extract-games"],
        capture_output=True, text=True, env=env, cwd=str(root),
    )


def test_deploy_falha_quando_carimbo_nao_volta_na_leitura(tmp_path):
    resultado = _run_extract_games(tmp_path, GCLOUD_STUB_SEM_CARIMBO_NA_LEITURA)

    assert resultado.returncode != 0, (
        "deploy_cloud_run.sh precisa sair com erro quando o carimbo nao volta na "
        "leitura de conferencia pos-deploy\n" + resultado.stdout
    )
    assert "não voltou na leitura de conferência" in resultado.stdout
    assert "esperado:" in resultado.stdout
    assert "<ausente>" in resultado.stdout


def test_deploy_passa_quando_o_stub_ecoa_o_carimbo_de_volta(tmp_path):
    """Controle: o mesmo cenário, mas com o stub do golden test (que ecoa de
    volta o `--set-env-vars` real) — prova que a falha acima é do carimbo ausente,
    não de algum outro efeito colateral do arranjo do teste."""
    resultado = _run_extract_games(tmp_path, base.GCLOUD_STUB)

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "Carimbo de procedência conferido" in resultado.stdout


if __name__ == "__main__":
    sys.exit(subprocess.run(["python3", "-m", "pytest", str(Path(__file__)), "-v"]).returncode)
