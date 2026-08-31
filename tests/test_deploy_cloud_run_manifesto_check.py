"""Guarda do cross-check fail-closed entre a tabela de deploy e o manifesto de
procedência (DE #51, `deploy_cloud_run.sh::checar_manifesto`).

Duas falsificações, nos dois sentidos:
1. Um serviço na tabela do deploy (`SERVICES`) sem cobertura no manifesto
   (`procedencia_servicos.sh`) — o manifesto "esqueceu" um serviço real.
2. Um serviço no manifesto sem entrada na tabela do deploy — o manifesto ficou com
   uma entrada órfã (ex.: serviço renomeado ou removido do deploy sem tirar do
   manifesto).

Os dois abortam ANTES de qualquer `gcloud run deploy`, mesmo pedindo o deploy de um
serviço não relacionado — a checagem é sobre a tabela inteira, não sobre o alvo.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_deploy_cloud_run_argv as base  # reusa _build_fake_repo, GCLOUD_STUB, SLEEP_STUB

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_deploy(root: Path, *args):
    stub_dir = root / "_stubs"
    stub_dir.mkdir(exist_ok=True)
    for name, body in (("gcloud", base.GCLOUD_STUB), ("sleep", base.SLEEP_STUB)):
        path = stub_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    capture_dir = root / "_argv"
    capture_dir.mkdir(exist_ok=True)

    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "CAPTURE_DIR": str(capture_dir),
        "HOME": str(root),
    }
    return subprocess.run(
        ["bash", str(root / "scripts" / "deploy_cloud_run.sh"), *args],
        capture_output=True, text=True, env=env, cwd=str(root),
    )


def test_servico_na_tabela_sem_manifesto_aborta_antes_do_deploy(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    base._build_fake_repo(root, api_football_key=False)

    deploy_script = root / "scripts" / "deploy_cloud_run.sh"
    texto = deploy_script.read_text(encoding="utf-8")
    # Acrescenta um serviço novo à tabela SEM declará-lo no manifesto (procedencia
    # continua intocado) — replica "alguém acrescentou um serviço e esqueceu o
    # manifesto".
    texto = texto.replace(
        'NBA_SERVICES=(\n    "extract-active-players:extract_active_players"',
        'NBA_SERVICES=(\n    "extract-servico-fantasma:extract_servico_fantasma"\n'
        '    "extract-active-players:extract_active_players"',
    )
    deploy_script.write_text(texto, encoding="utf-8")

    resultado = _run_deploy(root, "extract-games")

    assert resultado.returncode != 0
    assert "fora de sincronia" in resultado.stdout
    assert "extract-servico-fantasma" in resultado.stdout
    assert "AUSENTE do manifesto" in resultado.stdout
    # Aborta ANTES de qualquer deploy — nem o alvo pedido (extract-games) roda.
    argv_dir = root / "_argv"
    assert not any(argv_dir.glob("*.argv"))


def test_servico_no_manifesto_sem_tabela_aborta_antes_do_deploy(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    base._build_fake_repo(root, api_football_key=False)

    procedencia_script = root / "scripts" / "procedencia_servicos.sh"
    texto = procedencia_script.read_text(encoding="utf-8")
    # Acrescenta um nome novo em SERVICOS_CONHECIDOS (a lista que `--list-servicos`
    # devolve) sem existir na tabela do deploy — replica "manifesto com entrada órfã".
    assert "    daily-summary\n)" in texto
    texto = texto.replace(
        "    daily-summary\n)",
        "    daily-summary\n    extract-servico-orfao-no-manifesto\n)",
    )
    procedencia_script.write_text(texto, encoding="utf-8")

    resultado = _run_deploy(root, "extract-games")

    assert resultado.returncode != 0
    assert "fora de sincronia" in resultado.stdout
    assert "extract-servico-orfao-no-manifesto" in resultado.stdout
    assert "AUSENTE da tabela do deploy" in resultado.stdout
    argv_dir = root / "_argv"
    assert not any(argv_dir.glob("*.argv"))


def test_manifesto_em_sincronia_nao_bloqueia_deploy(tmp_path):
    """Controle: a árvore real (sem sabotagem) passa pelo cross-check e chega a
    deployar — prova que a falha dos dois testes acima é do cross-check, não de
    algum outro efeito colateral do arranjo do teste."""
    root = tmp_path / "repo"
    root.mkdir()
    base._build_fake_repo(root, api_football_key=False)

    resultado = _run_deploy(root, "extract-games")

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "fora de sincronia" not in resultado.stdout


if __name__ == "__main__":
    sys.exit(subprocess.run(["python3", "-m", "pytest", str(Path(__file__)), "-v"]).returncode)
