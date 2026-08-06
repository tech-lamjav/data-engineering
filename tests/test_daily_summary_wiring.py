"""Fiacao da secao de cota dentro do resumo diario.

src.reporting.daily_summary importa google-cloud-logging/workflows, que so existem no
requirements do Cloud Run (cloud_run/daily_summary/requirements.txt) e nao no de teste.
Aqui os dois SDKs viram stubs em sys.modules so o suficiente para conferir a FIACAO:
que os imports do modulo resolvem e que build_html concatena a secao de cota no lugar
certo. A logica de cota (leitura, limiares, formatacao) e testada de verdade, sem
stub nenhum, em tests/test_daily_summary_quota.py.
"""
import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.reporting.api_quota import QuotaInfo

DIA = date(2026, 8, 6)
LEITURA = datetime(2026, 8, 6, 3, 5, tzinfo=timezone.utc)

_STUBS_GCP = (
    "google.cloud.logging",
    "google.cloud.workflows",
    "google.cloud.workflows.executions_v1",
    "google.cloud.workflows.executions_v1.types",
)


@pytest.fixture
def daily_summary():
    """Importa daily_summary com os SDKs de GCP stubados e restaura sys.modules no fim."""
    alvos = (*_STUBS_GCP, "src.reporting.daily_summary")
    salvos = {nome: sys.modules.get(nome) for nome in alvos}
    for nome in _STUBS_GCP:
        sys.modules[nome] = MagicMock()
    sys.modules.pop("src.reporting.daily_summary", None)
    try:
        import src.reporting.daily_summary as mod

        yield mod
    finally:
        for nome, antigo in salvos.items():
            if antigo is None:
                sys.modules.pop(nome, None)
            else:
                sys.modules[nome] = antigo


def _quota():
    return QuotaInfo(
        current=7189,
        limit_day=7500,
        plan="Pro",
        subscription_end=date(2026, 8, 11),
        active=True,
        read_at=LEITURA,
    )


def test_build_html_inclui_a_secao_de_cota(daily_summary):
    _, html = daily_summary.build_html(DIA, {}, _quota())

    assert "Cota da API-Football" in html
    assert "7189" in html
    assert "ALERTA" in html  # 95,9% do limite e plano vencendo em 5 dias


def test_build_html_sem_cota_mantem_o_email_de_antes(daily_summary):
    _, html = daily_summary.build_html(DIA, {})

    assert "Cota da API-Football" not in html


def test_build_html_fecha_o_container_depois_da_secao(daily_summary):
    # Guarda a ordem da concatenacao: a secao entra ANTES do </div> de fechamento.
    _, html = daily_summary.build_html(DIA, {}, _quota())

    assert html.endswith("</div>")
    assert html.index("Cota da API-Football") < html.rindex("</div>")


def test_secao_degradada_nao_derruba_o_email(daily_summary):
    _, html = daily_summary.build_html(DIA, {}, QuotaInfo(error="ConnectionError: timeout"))

    assert "Cota da API-Football" in html
    assert "ConnectionError: timeout" in html
