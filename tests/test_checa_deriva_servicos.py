"""Guarda do `scripts/checa_deriva_servicos.sh` (DE #50 tracer bullet + DE #51 — 29
serviços + DE #52 — relatório agrupado por causa comum).

Exercita os quatro estados macro (em dia, deriva, sem carimbo, erro de leitura) e, para
deriva, as três causas que o `deploy_cloud_run.sh` grava à parte (núcleo, módulo
próprio, os dois) — contra um `gcloud` de mentira e um `procedencia_servicos.sh` de
mentira (hashes fixos, para não depender da árvore real).
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECA_SCRIPT = REPO_ROOT / "scripts" / "checa_deriva_servicos.sh"

HASH_COMBINADO = "abc123combinado"
HASH_NUCLEO = "aaa111nucleo"
HASH_SVC = "bbb222svc"

# Responde ao hash local: combinado sem flag, nucleo/svc com --nucleo/--svc, os três
# de uma vez com --todos (o que checa_deriva_servicos.sh usa de fato — DE #52).
FAKE_PROCEDENCIA = f"""#!/bin/bash
if [ "$1" = "--list-servicos" ]; then
    printf '%s\\n' em-dia deriva-nucleo deriva-svc deriva-ambos sem-carimbo inexistente
    exit 0
fi
case "$2" in
    --nucleo) echo "{HASH_NUCLEO}" ;;
    --svc) echo "{HASH_SVC}" ;;
    --todos)
        echo "{HASH_COMBINADO}"
        echo "{HASH_NUCLEO}"
        echo "{HASH_SVC}"
        ;;
    "") echo "{HASH_COMBINADO}" ;;
    *) echo "ERRO: flag desconhecida" >&2; exit 2 ;;
esac
"""

# O stub responde por `services describe`, ramificando pelo nome do serviço (`$4`,
# posicional em `run services describe <nome>`) — cada teste usa um nome de serviço
# diferente para escolher o cenário. Cada cenário devolve os TRÊS componentes
# (PROCEDENCIA_HASH{,_NUCLEO,_SVC}) — o script os lê juntos numa passada só.


def _json_com_env(combinado, nucleo, svc):
    envs = []
    if combinado is not None:
        envs.append({"name": "PROCEDENCIA_HASH", "value": combinado})
    if nucleo is not None:
        envs.append({"name": "PROCEDENCIA_HASH_NUCLEO", "value": nucleo})
    if svc is not None:
        envs.append({"name": "PROCEDENCIA_HASH_SVC", "value": svc})
    import json as _json
    return _json.dumps({"spec": {"template": {"spec": {"containers": [{"env": envs}]}}}})


GCLOUD_STUB = f"""#!/bin/bash
if [ "$1" = "run" ] && [ "$2" = "services" ] && [ "$3" = "describe" ]; then
    servico="$4"
    case "$servico" in
        em-dia)
            echo '{_json_com_env(HASH_COMBINADO, HASH_NUCLEO, HASH_SVC)}'
            exit 0
            ;;
        deriva-nucleo)
            echo '{_json_com_env("deadbeef", "deadnucleo", HASH_SVC)}'
            exit 0
            ;;
        deriva-svc)
            echo '{_json_com_env("deadbeef", HASH_NUCLEO, "deadsvc")}'
            exit 0
            ;;
        deriva-ambos)
            echo '{_json_com_env("deadbeef", "deadnucleo", "deadsvc")}'
            exit 0
            ;;
        sem-carimbo)
            echo '{_json_com_env(None, None, None)}'
            exit 0
            ;;
        sem-carimbo-sem-env)
            echo '{{"spec": {{"template": {{"spec": {{"containers": [{{}}]}}}}}}}}'
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


def test_em_dia_nao_imprime_linha_nenhuma(sandbox):
    """Serviço verde não imprime linha — verde é ausência de notícia."""
    resultado = run(sandbox, "em-dia")
    assert resultado.returncode == 0
    assert "em-dia" not in resultado.stdout
    assert "RESULTADO: todos os alvos em dia." in resultado.stdout


def test_deriva_nucleo_vira_linha_agrupada(sandbox):
    resultado = run(sandbox, "deriva-nucleo")
    assert resultado.returncode == 1
    assert "nucleo compartilhado derivou — 1 servico(s)" in resultado.stdout
    assert "./scripts/deploy_cloud_run.sh" in resultado.stdout
    assert "1 em deriva" in resultado.stdout


def test_deriva_svc_vira_linha_individual_sem_entrar_no_grupo(sandbox):
    resultado = run(sandbox, "deriva-svc")
    assert resultado.returncode == 1
    assert "nucleo compartilhado derivou" not in resultado.stdout
    assert "deriva-svc: modulo proprio derivou (nucleo em dia)" in resultado.stdout
    assert "deploy_cloud_run.sh deriva-svc" in resultado.stdout
    # Linha individual (não agrupada): o valor do hash continua disponível para debug.
    assert "no servico: deadbeef" in resultado.stdout
    assert f"no repo:    {HASH_COMBINADO}" in resultado.stdout


def test_deriva_ambos_entra_no_grupo_e_ganha_linha_adicional(sandbox):
    """Serviço que também divergiu por causa própria ganha linha adicional
    identificando isso — mas continua contado UMA vez no grupo do núcleo."""
    resultado = run(sandbox, "deriva-ambos")
    assert resultado.returncode == 1
    assert "nucleo compartilhado derivou — 1 servico(s)" in resultado.stdout
    assert "deriva-ambos: modulo proprio tambem derivou" in resultado.stdout
    assert "1 em deriva" in resultado.stdout  # contado 1x, nao 2x


def test_varios_com_a_mesma_causa_compartilhada_viram_uma_linha_so(sandbox):
    """3 serviços com o núcleo divergindo produzem UMA linha com contagem 3, não 3
    linhas repetindo a mesma notícia."""
    resultado = run(sandbox, "deriva-nucleo", "deriva-ambos", "em-dia")
    assert resultado.returncode == 1
    assert "nucleo compartilhado derivou — 2 servico(s)" in resultado.stdout
    assert resultado.stdout.count("nucleo compartilhado derivou") == 1


def test_sem_carimbo_sai_vermelho_com_texto_diferente_de_deriva(sandbox):
    resultado = run(sandbox, "sem-carimbo")
    assert resultado.returncode == 1
    assert "SEM CARIMBO" in resultado.stdout
    assert "modulo proprio" not in resultado.stdout
    assert "nucleo compartilhado" not in resultado.stdout
    assert "1 sem carimbo" in resultado.stdout


def test_sem_carimbo_quando_env_nem_existe_no_json(sandbox):
    resultado = run(sandbox, "sem-carimbo-sem-env")
    assert resultado.returncode == 1
    assert "SEM CARIMBO" in resultado.stdout


def test_erro_de_leitura_nao_conta_como_deriva(sandbox):
    resultado = run(sandbox, "inexistente")
    assert resultado.returncode == 1
    assert "erro de leitura" in resultado.stdout
    assert "NOT_FOUND" in resultado.stdout  # detalhe completo, nao só a 1a linha
    assert "nucleo compartilhado" not in resultado.stdout
    assert "SEM CARIMBO" not in resultado.stdout
    assert "0 em deriva" in resultado.stdout
    assert "1 erro" in resultado.stdout


def test_aviso_em_stderr_no_caminho_de_sucesso_nao_contamina_o_hash(tmp_path):
    """Regressão do achado do /code-review: stderr de um comando bem-sucedido (ex.:
    aviso do git) NÃO pode ser misturado ao valor comparado como hash — senão um
    serviço em dia sai reportado como deriva."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    checa_copy = scripts_dir / "checa_deriva_servicos.sh"
    checa_copy.write_text(CHECA_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    checa_copy.chmod(0o755)

    # Sucesso (exit 0) MAS emite aviso em stderr — simula um `git` barulhento.
    procedencia_barulhento = scripts_dir / "procedencia_servicos.sh"
    procedencia_barulhento.write_text(
        f"""#!/bin/bash
if [ "$1" = "--list-servicos" ]; then
    printf '%s\\n' em-dia-barulhento
    exit 0
fi
echo "warning: isso nao deveria contaminar o hash" >&2
if [ "$2" = "--todos" ]; then
    printf '{HASH_COMBINADO}\\n{HASH_NUCLEO}\\n{HASH_SVC}\\n'
fi
""",
        encoding="utf-8",
    )
    procedencia_barulhento.chmod(0o755)

    stub_dir = tmp_path / "_stubs"
    stub_dir.mkdir()
    (stub_dir / "gcloud").write_text(GCLOUD_STUB, encoding="utf-8")
    (stub_dir / "gcloud").chmod(0o755)

    env = {"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
    resultado = subprocess.run(
        ["bash", str(checa_copy), "em-dia"], capture_output=True, text=True, env=env,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "RESULTADO: todos os alvos em dia." in resultado.stdout


def test_sem_argumento_usa_a_lista_do_manifesto(sandbox):
    """Sem args, os alvos vêm de `procedencia_servicos.sh --list-servicos`
    (DE #51) — o fake devolve 6 nomes cobrindo todos os estados. Checa não só a
    contagem agregada, mas que é ESTE conjunto de serviços que foi processado — uma
    contagem certa por acidente (nomes trocados, mesmo total) não passaria."""
    resultado = run(sandbox)
    assert resultado.returncode == 1
    assert "6 alvos, 3 em deriva, 1 sem carimbo, 1 erro(s) de leitura" in resultado.stdout
    # em-dia é silencioso de propósito (verde não imprime linha) — o resto, cada um
    # tem de aparecer com o texto do próprio estado, provando que o alvo certo caiu
    # na categoria certa (não só que a contagem bateu).
    assert "deriva-nucleo" not in resultado.stdout  # entra só na contagem do grupo
    assert "nucleo compartilhado derivou — 2 servico(s)" in resultado.stdout  # nucleo + ambos
    assert "deriva-svc: modulo proprio derivou" in resultado.stdout
    assert "deriva-ambos: modulo proprio tambem derivou" in resultado.stdout
    assert "sem-carimbo: SEM CARIMBO" in resultado.stdout
    assert "inexistente: erro de leitura" in resultado.stdout


def test_sem_argumento_falha_fail_closed_se_list_servicos_falhar(tmp_path):
    """Se `procedencia_servicos.sh --list-servicos` falhar (não vazio-e-verde), o
    detector tem de abortar — não reportar 'todos os alvos em dia' sem checar nenhum."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    checa_copy = scripts_dir / "checa_deriva_servicos.sh"
    checa_copy.write_text(CHECA_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    checa_copy.chmod(0o755)

    procedencia_quebrado = scripts_dir / "procedencia_servicos.sh"
    procedencia_quebrado.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    procedencia_quebrado.chmod(0o755)

    resultado = subprocess.run(["bash", str(checa_copy)], capture_output=True, text=True)
    assert resultado.returncode != 0
    assert "todos os alvos em dia" not in resultado.stdout


def test_sem_argumento_falha_fail_closed_se_list_servicos_vier_vazio(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    checa_copy = scripts_dir / "checa_deriva_servicos.sh"
    checa_copy.write_text(CHECA_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    checa_copy.chmod(0o755)

    procedencia_vazio = scripts_dir / "procedencia_servicos.sh"
    procedencia_vazio.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    procedencia_vazio.chmod(0o755)

    resultado = subprocess.run(["bash", str(checa_copy)], capture_output=True, text=True)
    assert resultado.returncode != 0
    assert "todos os alvos em dia" not in resultado.stdout


def test_os_estados_juntos_contam_separado_e_cabem_numa_tela(sandbox):
    resultado = run(
        sandbox, "em-dia", "deriva-nucleo", "deriva-svc", "deriva-ambos",
        "sem-carimbo", "inexistente",
    )
    assert resultado.returncode == 1
    assert "6 alvos, 3 em deriva, 1 sem carimbo, 1 erro(s) de leitura" in resultado.stdout
    assert "RESULTADO: 3 em deriva, 1 sem carimbo, 1 erro(s) de leitura." in resultado.stdout
    # "cabe numa tela": nenhuma linha de serviço individual para os 2 que derivaram
    # pelo núcleo (deriva-nucleo some no grupo; deriva-ambos ganha só a linha extra).
    assert resultado.stdout.count("nucleo compartilhado derivou") == 1
    linhas = [l for l in resultado.stdout.splitlines() if l.strip()]
    assert len(linhas) < 20


def test_os_29_em_deriva_pelo_nucleo_cabem_numa_tela(sandbox):
    """AC literal da DE #52: com os 29 em deriva pelo núcleo, a saída inteira cabe
    numa tela — não 29 linhas repetindo a mesma notícia."""
    resultado = run(sandbox, *(["deriva-nucleo"] * 29))
    assert resultado.returncode == 1
    assert "nucleo compartilhado derivou — 29 servico(s)" in resultado.stdout
    linhas = [l for l in resultado.stdout.splitlines() if l.strip()]
    assert len(linhas) <= 15, f"saida com {len(linhas)} linhas nao livre numa tela:\n{resultado.stdout}"


if __name__ == "__main__":
    import sys
    sys.exit(subprocess.run(["python3", "-m", "pytest", str(Path(__file__)), "-v"]).returncode)
