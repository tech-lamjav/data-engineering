"""Guarda de equivalência do `scripts/deploy_cloud_run.sh`.

Roda o script de deploy inteiro contra um `gcloud` de mentira e congela o argv de
cada `gcloud run deploy` num golden. É o detector do prefactor da DE #49: as quatro
ramificações do `deploy_service()` passaram a derivar as env vars de um ponto único,
e "não mudou nada" precisa ser fato conferível e não promessa no comentário.

Cobre os 29 serviços de uma vez e o argv **completo** — não só `--set-env-vars` —,
para que as diferenças legítimas entre ramificações (2Gi e `--max-instances` do
`sync-bq-to-postgres`, secrets do `notify-execution`, `--set-build-env-vars`
ausente no `notify-execution`) também fiquem presas.

A árvore é falsa e o `.env` é sintético: o golden não pode depender do `.env` real
da máquina (nem carregar segredo nenhum para dentro do repositório).

Regenerar depois de uma mudança **intencional** (é o que a DE #50 vai fazer, ao
acrescentar as quatro env vars de carimbo):

    python3 tests/test_deploy_cloud_run_argv.py --update
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_cloud_run.sh"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "deploy_cloud_run_argv.json"

# .env sintético: valores fixos para o golden não variar com a máquina.
FAKE_ENV = {
    "BALLDONTLIE_KEY": "fake-balldontlie-key",
    "GCS_BUCKET_NAME": "fake-bucket",
    "GCP_PROJECT_ID": "fake-project",
    "SEASON": "2099",
    "LOG_LEVEL": "DEBUG",
    "SERVICE_ACCOUNT": "FakeSA",
}

# O stub responde o que o script consulta: autenticação, número do projeto e as
# duas leituras pós-deploy (URL e condição de readiness). Todo `gcloud run deploy`
# vira um arquivo de argv, um argumento por linha.
GCLOUD_STUB = r"""#!/bin/bash
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
        esac
    done
fi
exit 0
"""

SLEEP_STUB = "#!/bin/bash\nexit 0\n"


def _service_dirs():
    """Extrai `cloud_run/<dir>` de cada serviço das listas do próprio script."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    dirs = []
    for line in text.splitlines():
        line = line.strip()
        if not (line.startswith('"') and line.endswith('"') and ":" in line):
            continue
        entry = line.strip('"')
        name, _, service_dir = entry.partition(":")
        if name and service_dir and "=" not in entry and " " not in entry:
            dirs.append(service_dir)
    assert len(dirs) == 29, f"esperado 29 serviços nas listas do script, achei {len(dirs)}"
    return dirs


def _build_fake_repo(root: Path, api_football_key: bool):
    """Árvore mínima com a mesma forma que o `deploy_service()` valida."""
    (root / "src").mkdir()
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "scripts").mkdir()
    shutil.copy2(DEPLOY_SCRIPT, root / "scripts" / "deploy_cloud_run.sh")

    for service_dir in _service_dirs():
        target = root / "cloud_run" / service_dir
        target.mkdir(parents=True)
        (target / "main.py").write_text("", encoding="utf-8")
        (target / "requirements.txt").write_text("", encoding="utf-8")
        # Procfile só onde ele existe de verdade — o `deploy_service()` ramifica nisso.
        if (REPO_ROOT / "cloud_run" / service_dir / "Procfile").exists():
            (target / "Procfile").write_text("", encoding="utf-8")

    env = dict(FAKE_ENV)
    if api_football_key:
        env["API_FOOTBALL_KEY"] = "fake-api-football-key"
    (root / ".env").write_text(
        "".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8"
    )


def capture_deploy_argv(tmp_path: Path, api_football_key: bool):
    """Roda o deploy dos 29 serviços contra o stub e devolve {serviço: argv}."""
    root = tmp_path / ("com-chave" if api_football_key else "sem-chave")
    root.mkdir()
    _build_fake_repo(root, api_football_key)

    stub_dir = root / "_stubs"
    stub_dir.mkdir()
    for name, body in (("gcloud", GCLOUD_STUB), ("sleep", SLEEP_STUB)):
        path = stub_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    capture_dir = root / "_argv"
    capture_dir.mkdir()

    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "CAPTURE_DIR": str(capture_dir),
        "HOME": str(root),
    }
    result = subprocess.run(
        ["bash", str(root / "scripts" / "deploy_cloud_run.sh")],
        capture_output=True, text=True, env=env, cwd=str(root),
    )
    assert result.returncode == 0, (
        f"deploy_cloud_run.sh falhou:\n{result.stdout}\n{result.stderr}"
    )

    captured = {}
    for argv_file in sorted(capture_dir.glob("*.argv")):
        argv = argv_file.read_text(encoding="utf-8").splitlines()
        # O `--source` é um mktemp -d: aleatório por execução, logo normalizado.
        for i, arg in enumerate(argv):
            if i > 0 and argv[i - 1] == "--source":
                argv[i] = "<TEMP_DIR>"
        captured[argv_file.stem] = argv
    return captured


def snapshot(tmp_path: Path):
    return {
        "com_api_football_key": capture_deploy_argv(tmp_path, True),
        "sem_api_football_key": capture_deploy_argv(tmp_path, False),
    }


@pytest.fixture(scope="module")
def argv_snapshot(tmp_path_factory):
    return snapshot(tmp_path_factory.mktemp("deploy"))


@pytest.mark.parametrize("cenario", ["com_api_football_key", "sem_api_football_key"])
def test_argv_do_deploy_bate_com_o_golden(argv_snapshot, cenario):
    esperado = json.loads(GOLDEN.read_text(encoding="utf-8"))[cenario]
    assert argv_snapshot[cenario] == esperado


def test_os_29_servicos_sao_deployados(argv_snapshot):
    assert len(argv_snapshot["com_api_football_key"]) == 29
    assert len(argv_snapshot["sem_api_football_key"]) == 29


if __name__ == "__main__":
    import tempfile

    if "--update" not in sys.argv:
        print("uso: python3 tests/test_deploy_cloud_run_argv.py --update")
        raise SystemExit(2)
    with tempfile.TemporaryDirectory() as tmp:
        GOLDEN.write_text(
            json.dumps(snapshot(Path(tmp)), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(f"golden regravado: {GOLDEN}")
