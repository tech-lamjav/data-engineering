"""Guarda do `scripts/procedencia_servicos.sh` (DE #50 tracer bullet + DE #51 — 29 serviços).

Cobre o que a issue pede como aceite: hash determinístico, `--paths`, fail-closed
quando uma path declarada some do disco, e o terceiro estado ("fora de escopo", exit 3)
para o diretório órfão `extract-player-props` — distinto de erro de path (exit 1) e de
erro de uso (exit 2), porque `deploy_cloud_run.sh` reage diferente a cada um.

Também cobre o cross-check fail-closed da DE #51: `--list-servicos` (os 29 do
manifesto) tem de bater 1:1 com a tabela `NBA_SERVICES`/`FUTEBOL_SERVICES`/
`SHARED_SERVICES` de `deploy_cloud_run.sh` — os dois arquivos evoluem juntos.
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "procedencia_servicos.sh"
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_cloud_run.sh"


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


def test_diretorio_orfao_fica_fora_do_manifesto_de_proposito():
    """`cloud_run/extract_player_props/` foi substituído pelas 3 variantes por vendor
    e não é deployado por `deploy_cloud_run.sh` — exit 3, não erro de path."""
    resultado = run("extract-player-props")
    assert resultado.returncode == 3
    assert "extract-player-props" in resultado.stderr


def test_nome_desconhecido_sai_3_e_nao_1():
    resultado = run("extract-um-servico-que-nao-existe")
    assert resultado.returncode == 3


def test_list_servicos_lista_os_29_e_sai_zero():
    resultado = run("--list-servicos")
    assert resultado.returncode == 0
    servicos = [l for l in resultado.stdout.splitlines() if l.strip()]
    assert len(servicos) == 29
    assert len(set(servicos)) == 29  # sem duplicata
    assert "extract-games" in servicos
    assert "daily-summary" in servicos
    # O órfão nunca aparece na lista coberta.
    assert "extract-player-props" not in servicos


def test_todo_servico_de_list_servicos_tem_paths_declaradas():
    servicos = run("--list-servicos").stdout.splitlines()
    for servico in servicos:
        resultado = run(servico, "--paths")
        assert resultado.returncode == 0, f"{servico}: --list-servicos promete cobertura que --paths nao entrega"
        assert resultado.stdout.strip() != ""


def _service_names_from_deploy_script():
    """Extrai os nomes de serviço (antes do `:`) das tabelas NBA_SERVICES /
    FUTEBOL_SERVICES / SHARED_SERVICES de `deploy_cloud_run.sh` — mesma técnica de
    `tests/test_deploy_cloud_run_argv.py::_service_dirs`."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    nomes = []
    for line in text.splitlines():
        line = line.strip()
        if not (line.startswith('"') and line.endswith('"') and ":" in line):
            continue
        entry = line.strip('"')
        name, _, service_dir = entry.partition(":")
        if name and service_dir and "=" not in entry and " " not in entry:
            nomes.append(name)
    return nomes


def test_list_servicos_bate_1_para_1_com_a_tabela_do_deploy():
    """O cross-check fail-closed que `deploy_cloud_run.sh::checar_manifesto` roda em
    runtime, congelado como teste: os dois arquivos precisam concordar exatamente
    sobre quais 29 serviços existem — nenhum a mais, nenhum a menos."""
    do_manifesto = set(run("--list-servicos").stdout.splitlines())
    da_tabela_de_deploy = set(_service_names_from_deploy_script())
    assert do_manifesto == da_tabela_de_deploy


def test_futebol_com_scripts_declara_scripts_futebol():
    """9 dos 13 de futebol seguem o mesmo padrão do NBA (`from extract_X import
    main`, resolve em `scripts/futebol/extract_X.py`) — `extract-leagues` é um deles."""
    resultado = run("extract-leagues", "--paths")
    assert resultado.returncode == 0
    linhas = resultado.stdout.splitlines()
    assert "scripts/futebol/extract_leagues.py" in linhas
    assert "src/extractors/leagues_extractor.py" in linhas


def test_futebol_sem_scripts_nao_declara_scripts_futebol():
    """odds/predictions/fixture_lineups chamam o extractor de `src/extractors/`
    DIRETO — `scripts/futebol/extract_odds.py` existe no disco mas não é importado
    e não entra no manifesto (ver cabeçalho do script)."""
    resultado = run("extract-odds", "--paths")
    assert resultado.returncode == 0
    linhas = resultado.stdout.splitlines()
    assert "scripts/futebol/extract_odds.py" not in linhas
    assert "src/extractors/odds_extractor.py" in linhas


def test_injuries_declara_os_dois_caminhos():
    """current/backfill passam por scripts/futebol/, pregame chama o extractor
    direto — os dois estão em produção (ver cloud_run/futebol/extract_injuries/main.py)."""
    resultado = run("extract-injuries", "--paths")
    assert resultado.returncode == 0
    linhas = resultado.stdout.splitlines()
    assert "scripts/futebol/extract_injuries.py" in linhas
    assert "src/extractors/injuries_extractor.py" in linhas


def test_daily_summary_declara_src_reporting_como_diretorio():
    resultado = run("daily-summary", "--paths")
    assert resultado.returncode == 0
    linhas = resultado.stdout.splitlines()
    assert "src/reporting" in linhas
    assert "cloud_run/daily_summary/main.py" in linhas
    assert "cloud_run/daily_summary/Procfile" in linhas


def test_sync_bq_to_postgres_declara_src_sync_como_diretorio():
    resultado = run("sync-bq-to-postgres", "--paths")
    assert resultado.returncode == 0
    linhas = resultado.stdout.splitlines()
    assert "src/sync" in linhas


def test_nenhum_servico_de_nba_declara_path_de_futebol():
    """Falsificação do ADR (decisão A′): um commit em `scripts/futebol/` não pode
    derivar nenhum serviço de NBA — 13 commits fizeram isso no ingênuo pré-#51."""
    nba = [
        "extract-active-players", "extract-games", "extract-game-player-stats",
        "extract-game-player-stats-period", "extract-game-player-advanced-stats",
        "extract-season-averages", "extract-team-season-averages",
        "extract-player-injuries", "extract-team-standings",
        "extract-player-props-draftkings", "extract-player-props-caesars",
        "extract-player-props-betrivers", "extract-betting-odds",
    ]
    for servico in nba:
        linhas = run(servico, "--paths").stdout.splitlines()
        for linha in linhas:
            assert "futebol" not in linha, f"{servico} declara path de futebol: {linha}"


def test_src_config_py_esta_no_nucleo_dos_29():
    """Falsificação do ADR: um commit em `src/config.py` deriva os 29 — é a verdade
    que o relatório agrupado (DE #52) precisa apresentar como uma linha só."""
    for servico in run("--list-servicos").stdout.splitlines():
        linhas = run(servico, "--paths").stdout.splitlines()
        assert "src/config.py" in linhas, f"{servico} nao inclui src/config.py no nucleo"


def test_todos_os_29_hasheiam_sem_erro_contra_a_arvore_real():
    """Spot-check pesado: roda o hash combinado real (não só --paths) para os 29 —
    prova que cada path declarada existe de verdade no disco desta árvore, não só
    que o manifesto tem uma entrada com esse nome."""
    for servico in run("--list-servicos").stdout.splitlines():
        resultado = run(servico)
        assert resultado.returncode == 0, f"{servico}: {resultado.stderr}"
        assert resultado.stdout.strip() != ""


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
