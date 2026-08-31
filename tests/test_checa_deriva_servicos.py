"""Guarda do `scripts/checa_deriva_servicos.sh` (DE #50 — tracer bullet).

Exercita os quatro estados que a issue pede como falsificação, contra um `gcloud` de
mentira e um `procedencia_servicos.sh` de mentira (hash fixo, para não depender da
árvore real): em dia, deriva, sem carimbo (texto e remédio diferentes de deriva) e
erro de leitura (nunca contado como deriva).
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECA_SCRIPT = REPO_ROOT / "scripts" / "checa_deriva_servicos.sh"

HASH_ESPERADO = "abc123esperado"

FAKE_PROCEDENCIA = f"""#!/bin/bash
echo "{HASH_ESPERADO}"
"""

# O stub responde por `services describe`, ramificando pelo nome do serviço (`$3`) —
# cada teste usa um nome de serviço diferente para escolher o cenário.
GCLOUD_STUB = r"""#!/bin/bash
if [ "$1" = "run" ] && [ "$2" = "services" ] && [ "$3" = "describe" ]; then
    servico="$4"
    case "$servico" in
        em-dia)
            cat <<'JSON'
{"spec": {"template": {"spec": {"containers": [{"env": [{"name": "PROCEDENCIA_HASH", "value": "abc123esperado"}]}]}}}}
JSON
            exit 0
            ;;
        em-deriva)
            cat <<'JSON'
{"spec": {"template": {"spec": {"containers": [{"env": [{"name": "PROCEDENCIA_HASH", "value": "deadbeef"}]}]}}}}
JSON
            exit 0
            ;;
        sem-carimbo)
            cat <<'JSON'
{"spec": {"template": {"spec": {"containers": [{"env": [{"name": "OUTRA_VAR", "value": "x"}]}]}}}}
JSON
            exit 0
            ;;
        sem-carimbo-sem-env)
            cat <<'JSON'
{"spec": {"template": {"spec": {"containers": [{}]}}}}
JSON
            exit 0
            ;;
        inexistente)
            echo "ERROR: (gcloud.run.services.describe) NOT_FOUND" >&2
            exit 1
            ;;
    esac
fi
exit 1
"""


@pytest.fixture()
def sandbox(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    checa_copy = scripts_dir / "checa_deriva_servicos.sh"
    checa_copy.write_text(CHECA_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    checa_copy.chmod(0o755)

    procedencia_fake = scripts_dir / "procedencia_servicos.sh"
    procedencia_fake.write_text(FAKE_PROCEDENCIA, encoding="utf-8")
    procedencia_fake.chmod(0o755)

    stub_dir = tmp_path / "_stubs"
    stub_dir.mkdir()
    gcloud_stub = stub_dir / "gcloud"
    gcloud_stub.write_text(GCLOUD_STUB, encoding="utf-8")
    gcloud_stub.chmod(0o755)

    return checa_copy, stub_dir


def run(sandbox, *servicos):
    checa_copy, stub_dir = sandbox
    env = {"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", str(checa_copy), *servicos],
        capture_output=True, text=True, env=env,
    )


def test_em_dia_sai_verde(sandbox):
    resultado = run(sandbox, "em-dia")
    assert resultado.returncode == 0
    assert "em dia" in resultado.stdout
    assert "RESULTADO: todos os alvos em dia." in resultado.stdout


def test_deriva_sai_vermelho_com_texto_de_deriva(sandbox):
    resultado = run(sandbox, "em-deriva")
    assert resultado.returncode == 1
    assert "DERIVA" in resultado.stdout
    assert "1 em deriva" in resultado.stdout


def test_sem_carimbo_sai_vermelho_com_texto_diferente_de_deriva(sandbox):
    resultado = run(sandbox, "sem-carimbo")
    assert resultado.returncode == 1
    assert "SEM CARIMBO" in resultado.stdout
    assert "DERIVA" not in resultado.stdout
    assert "1 sem carimbo" in resultado.stdout


def test_sem_carimbo_quando_env_nem_existe_no_json(sandbox):
    resultado = run(sandbox, "sem-carimbo-sem-env")
    assert resultado.returncode == 1
    assert "SEM CARIMBO" in resultado.stdout


def test_erro_de_leitura_nao_conta_como_deriva(sandbox):
    resultado = run(sandbox, "inexistente")
    assert resultado.returncode == 1
    assert "ERRO" in resultado.stdout
    assert "DERIVA" not in resultado.stdout
    assert "SEM CARIMBO" not in resultado.stdout
    assert "0 em deriva" in resultado.stdout
    assert "1 erro" in resultado.stdout


def test_os_quatro_estados_juntos_contam_separado(sandbox):
    resultado = run(sandbox, "em-dia", "em-deriva", "sem-carimbo", "inexistente")
    assert resultado.returncode == 1
    assert "1 em deriva, 1 sem carimbo, 1 erro(s) de leitura" in resultado.stdout


if __name__ == "__main__":
    import sys
    sys.exit(subprocess.run(["python3", "-m", "pytest", str(Path(__file__)), "-v"]).returncode)
