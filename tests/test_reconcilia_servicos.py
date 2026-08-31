"""Guarda do `scripts/reconcilia_servicos.sh` (DE #53).

Cobre o quinto estado do ADR — órfão — que `checa_deriva_servicos.sh` não vigia de
propósito: serviço VIVO no Cloud Run e ausente do manifesto, e o espelho (entrada do
manifesto nunca deployada). Contra um `gcloud` de mentira (`run services list`) e um
`procedencia_servicos.sh` de mentira (`--list-servicos`).
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "reconcilia_servicos.sh"


def _gcloud_stub(live_servicos, falha=False):
    if falha:
        return "#!/bin/bash\necho 'ERROR: (gcloud.run.services.list) PERMISSION_DENIED' >&2\nexit 1\n"
    linhas = "\\n".join(live_servicos)
    return f"""#!/bin/bash
if [ "$1" = "run" ] && [ "$2" = "services" ] && [ "$3" = "list" ]; then
    printf '{linhas}\\n'
    exit 0
fi
exit 1
"""


def _procedencia_stub(manifesto_servicos, falha=False, vazio=False):
    if falha:
        return "#!/bin/bash\nexit 1\n"
    if vazio:
        return "#!/bin/bash\nexit 0\n"
    linhas = "\\n".join(manifesto_servicos)
    return f"""#!/bin/bash
if [ "$1" = "--list-servicos" ]; then
    printf '{linhas}\\n'
    exit 0
fi
exit 1
"""


def _sandbox(tmp_path, live, manifesto, gcloud_falha=False, procedencia_falha=False, procedencia_vazio=False):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    recon_copy = scripts_dir / "reconcilia_servicos.sh"
    recon_copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    recon_copy.chmod(0o755)

    procedencia = scripts_dir / "procedencia_servicos.sh"
    procedencia.write_text(
        _procedencia_stub(manifesto, falha=procedencia_falha, vazio=procedencia_vazio),
        encoding="utf-8",
    )
    procedencia.chmod(0o755)

    stub_dir = tmp_path / "_stubs"
    stub_dir.mkdir()
    gcloud_stub = stub_dir / "gcloud"
    gcloud_stub.write_text(_gcloud_stub(live, falha=gcloud_falha), encoding="utf-8")
    gcloud_stub.chmod(0o755)

    return recon_copy, stub_dir


def run(recon_copy, stub_dir):
    env = {"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(["bash", str(recon_copy)], capture_output=True, text=True, env=env)


def test_conjuntos_identicos_reconcilia_verde(tmp_path):
    servicos = ["extract-games", "extract-teams", "daily-summary"]
    recon, stubs = _sandbox(tmp_path, live=servicos, manifesto=servicos)
    resultado = run(recon, stubs)
    assert resultado.returncode == 0
    assert "reconciliado" in resultado.stdout
    assert "ORFAO" not in resultado.stdout
    assert "NUNCA DEPLOYADO" not in resultado.stdout


def test_servico_vivo_fora_do_manifesto_e_orfao_vivo(tmp_path):
    recon, stubs = _sandbox(
        tmp_path,
        live=["extract-games", "servico-fantasma"],
        manifesto=["extract-games"],
    )
    resultado = run(recon, stubs)
    assert resultado.returncode == 1
    assert "servico-fantasma: ORFAO VIVO" in resultado.stdout
    assert "1 orfao(s) vivo(s)" in resultado.stdout
    assert "0 nunca deployado" in resultado.stdout


def test_entrada_do_manifesto_nunca_deployada(tmp_path):
    recon, stubs = _sandbox(
        tmp_path,
        live=["extract-games"],
        manifesto=["extract-games", "extract-nao-existe-no-cloud-run"],
    )
    resultado = run(recon, stubs)
    assert resultado.returncode == 1
    assert "extract-nao-existe-no-cloud-run: NUNCA DEPLOYADO" in resultado.stdout
    assert "1 nunca deployado" in resultado.stdout
    assert "0 orfao" in resultado.stdout


def test_os_dois_estados_juntos(tmp_path):
    recon, stubs = _sandbox(
        tmp_path,
        live=["extract-games", "servico-fantasma"],
        manifesto=["extract-games", "extract-nunca-deployado"],
    )
    resultado = run(recon, stubs)
    assert resultado.returncode == 1
    assert "servico-fantasma: ORFAO VIVO" in resultado.stdout
    assert "extract-nunca-deployado: NUNCA DEPLOYADO" in resultado.stdout
    assert "1 orfao(s) vivo(s), 1 nunca deployado(s)" in resultado.stdout


def test_falsificacao_deployar_e_depois_remover_o_orfao(tmp_path):
    """A falsificação do ADR: deployar um serviço fora do manifesto faz a
    reconciliação acusá-lo; removê-lo devolve o verde."""
    servicos = ["extract-games"]

    recon, stubs = _sandbox(tmp_path / "1-antes", live=servicos, manifesto=servicos)
    assert run(recon, stubs).returncode == 0

    recon2, stubs2 = _sandbox(
        tmp_path / "2-sujo", live=servicos + ["orfao-recem-deployado"], manifesto=servicos,
    )
    resultado_sujo = run(recon2, stubs2)
    assert resultado_sujo.returncode == 1
    assert "orfao-recem-deployado: ORFAO VIVO" in resultado_sujo.stdout

    recon3, stubs3 = _sandbox(tmp_path / "3-depois", live=servicos, manifesto=servicos)
    resultado_limpo = run(recon3, stubs3)
    assert resultado_limpo.returncode == 0


def test_gcloud_falhar_aborta_fail_closed_nao_reporta_verde(tmp_path):
    recon, stubs = _sandbox(tmp_path, live=[], manifesto=["extract-games"], gcloud_falha=True)
    resultado = run(recon, stubs)
    assert resultado.returncode == 2
    assert "reconciliado" not in resultado.stdout
    assert "PERMISSION_DENIED" in resultado.stderr


def test_manifesto_falhar_aborta_fail_closed(tmp_path):
    recon, stubs = _sandbox(tmp_path, live=["extract-games"], manifesto=[], procedencia_falha=True)
    resultado = run(recon, stubs)
    assert resultado.returncode == 2
    assert "reconciliado" not in resultado.stdout


def test_manifesto_vazio_aborta_fail_closed(tmp_path):
    recon, stubs = _sandbox(tmp_path, live=["extract-games"], manifesto=[], procedencia_vazio=True)
    resultado = run(recon, stubs)
    assert resultado.returncode == 2
    assert "reconciliado" not in resultado.stdout


def test_isento_por_lista_explicita_nao_vira_orfao(tmp_path):
    """AC: serviço fora de escopo é isentado por lista explícita com motivo escrito,
    nunca por filtro silencioso de nome."""
    recon_copy, stub_dir = _sandbox(
        tmp_path,
        live=["extract-games", "servico-de-outro-time"],
        manifesto=["extract-games"],
    )
    texto = recon_copy.read_text(encoding="utf-8")
    assert "ISENTOS=()" in texto
    texto = texto.replace(
        "ISENTOS=()",
        'ISENTOS=("servico-de-outro-time:pertence a outro time, mesmo projeto/regiao — ver PR de teste")',
    )
    recon_copy.write_text(texto, encoding="utf-8")

    resultado = run(recon_copy, stub_dir)
    assert resultado.returncode == 0
    assert "servico-de-outro-time" not in resultado.stdout
    assert "reconciliado" in resultado.stdout


if __name__ == "__main__":
    import sys
    sys.exit(subprocess.run(["python3", "-m", "pytest", str(Path(__file__)), "-v"]).returncode)
