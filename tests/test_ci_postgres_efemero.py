"""O CI roda a integração da carga por troca contra um Postgres 17 de verdade (DE#108, spec #112).

Os mocks de `tests/test_sync_troca.py` não pegam regressão de lock, de fila de RENAME nem de
fidelidade de ACL/RLS; só `tests/test_sync_troca_integracao.py` pega, e ele é PULADO sem
`SYNC_TESTE_PG_URL`. Sem um banco no CI o pytest verde só cobria os mocks. Aqui ficam as duas
guardas que impedem esse buraco de voltar sem alarme: o workflow sobe o banco e a ausência dele no
CI FALHA em vez de pular.
"""
import os
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "testes.yml"


def _job_pytest():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["pytest"]


def test_o_workflow_de_testes_sobe_um_postgres_17_como_servico():
    servicos = _job_pytest()["services"]
    imagens = [s["image"] for s in servicos.values()]
    assert "postgres:17" in imagens
    postgres = next(s for s in servicos.values() if s["image"] == "postgres:17")
    assert "pg_isready" in postgres["options"], "sem health check o pytest corre antes do banco subir"
    assert "5432:5432" in [str(p) for p in postgres["ports"]]


def test_o_passo_do_pytest_aponta_a_variavel_da_integracao_para_o_servico():
    passos = [p for p in _job_pytest()["steps"] if "pytest" in str(p.get("run", ""))]
    assert passos, "sem passo que rode o pytest"
    url = passos[0]["env"]["SYNC_TESTE_PG_URL"]
    assert url.startswith("postgresql://") and "localhost:5432" in url
    # o teste de integração só aceita localhost (ele cria papéis e schema no cluster apontado)
    assert "localhost" in url


def test_no_ci_a_ausencia_do_banco_de_integracao_falha_em_vez_de_pular():
    """Fora do CI (máquina de quem desenvolve) a integração segue opcional e este teste passa."""
    if os.getenv("CI") and not os.getenv("SYNC_TESTE_PG_URL"):
        raise AssertionError(
            "CI sem SYNC_TESTE_PG_URL: os testes de integração da troca seriam PULADOS e o CI "
            "verde só cobriria os mocks. Suba o serviço postgres:17 no workflow de testes."
        )
