"""Guarda do `scripts/procedencia_servicos.sh` (DE #50 — tracer bullet).

Cobre o que a issue pede como aceite: hash determinístico, `--paths`, fail-closed
quando uma path declarada some do disco, e o terceiro estado ("ainda não coberto",
exit 3) para qualquer serviço fora do manifesto — distinto de erro de path (exit 1)
e de erro de uso (exit 2), porque `deploy_cloud_run.sh` reage diferente a cada um.
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "procedencia_servicos.sh"


def run(*args, cwd=REPO_ROOT, script=SCRIPT):
    return subprocess.run(
        ["bash", str(script), *args], capture_output=True, text=True, cwd=str(cwd)
    )


def test_hash_combinado_e_deterministico_entre_execucoes():
    primeira = run("extract-games")
    segunda = run("extract-games")
    assert primeira.returncode == 0
    assert primeira.stdout == segunda.stdout
    assert primeira.stdout.strip() != ""


def test_hash_combinado_dependende_dos_dois_componentes():
    combinado = run("extract-games").stdout.strip()
    nucleo = run("extract-games", "--nucleo").stdout.strip()
    svc = run("extract-games", "--svc").stdout.strip()

    assert combinado != "" and nucleo != "" and svc != ""
    # Os três hashes de um manifesto não-trivial não podem colidir entre si — se
    # colidirem, o `--nucleo`/`--svc` não está isolando nada.
    assert len({combinado, nucleo, svc}) == 3


def test_paths_lista_as_paths_declaradas_e_sai_sem_hashear():
    resultado = run("extract-games", "--paths")
    assert resultado.returncode == 0
    linhas = resultado.stdout.splitlines()
    assert "src/config.py" in linhas
    assert "cloud_run/extract_games/main.py" in linhas
    assert "scripts/extract_games.py" in linhas


def test_servico_nao_declarado_sai_3_e_nao_1():
    resultado = run("extract-active-players")
    assert resultado.returncode == 3
    assert "extract-active-players" in resultado.stderr
    assert "DE #51" in resultado.stderr


def test_sem_argumento_sai_2():
    resultado = run()
    assert resultado.returncode == 2


def test_flag_desconhecida_sai_2():
    resultado = run("extract-games", "--bogus")
    assert resultado.returncode == 2


def test_path_declarada_ausente_aborta_fail_closed(tmp_path):
    """Simula uma path do manifesto sumindo do disco: fail-closed, não silêncio."""
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=fake_repo, check=True)

    (fake_repo / "scripts").mkdir()
    script_copy = fake_repo / "scripts" / "procedencia_servicos.sh"
    script_copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    script_copy.chmod(0o755)

    # Cria só ALGUMAS das paths declaradas para extract-games — falta
    # `src/clients`, `src/storage`, `src/utils`, `src/bigquery` de propósito.
    (fake_repo / "src").mkdir()
    (fake_repo / "src" / "config.py").write_text("", encoding="utf-8")
    (fake_repo / "cloud_run" / "extract_games").mkdir(parents=True)
    (fake_repo / "cloud_run" / "extract_games" / "requirements.txt").write_text("", encoding="utf-8")
    (fake_repo / "cloud_run" / "extract_games" / "main.py").write_text("", encoding="utf-8")
    (fake_repo / "src" / "extractors").mkdir()
    (fake_repo / "src" / "extractors" / "games_extractor.py").write_text("", encoding="utf-8")
    (fake_repo / "scripts" / "extract_games.py").write_text("", encoding="utf-8")

    resultado = run("extract-games", cwd=fake_repo, script=script_copy)
    assert resultado.returncode == 1
    assert "src/clients" in resultado.stderr


if __name__ == "__main__":
    import sys
    sys.exit(subprocess.run(["python3", "-m", "pytest", str(Path(__file__)), "-v"]).returncode)
