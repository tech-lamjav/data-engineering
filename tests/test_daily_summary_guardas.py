"""C4: guarda vermelha tem que aparecer no resumo diario.

O e-mail diario e o UNICO canal de alarme que existe para as guardas de dado
(`tag:guarda`). Os workflows futebol/odds emitem `guardas_status` no log_completion,
de proposito separado de `status`: guarda vermelha nao vira PARTIAL_FAILURE do
workflow (nao pode derrubar o board), ela so reporta.

O caso discriminante e justamente esse: **workflow verde + guarda vermelha**. Se o
resumo so olha `status`, a linha sai 100% verde e a guarda vermelha some.
"""
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

DIA = date(2026, 8, 6)
INICIO = datetime(2026, 8, 6, 3, 0, tzinfo=timezone.utc)
FIM = datetime(2026, 8, 7, 3, 0, tzinfo=timezone.utc)
QUANDO = datetime(2026, 8, 6, 17, 4, tzinfo=timezone.utc)

_STUBS_GCP = (
    "google.cloud.logging",
    "google.cloud.workflows",
    "google.cloud.workflows.executions_v1",
    "google.cloud.workflows.executions_v1.types",
)


@pytest.fixture
def daily_summary():
    """Mesma fiacao de stubs do test_daily_summary_wiring."""
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


class _Entry:
    """Entrada do Cloud Logging: so o que collect_from_logging le."""

    def __init__(self, payload, timestamp=QUANDO):
        self.payload = payload
        self.timestamp = timestamp


class _FakeLogging:
    def __init__(self, entries):
        self._entries = entries

    def list_entries(self, **kwargs):
        return iter(self._entries)


def _payload(**extra):
    """log_completion do workflow_futebol_odds, campo a campo como o YAML emite."""
    base = {
        "message": "Workflow odds (pregame) completed",
        "workflow_name": "workflow_futebol_odds",
        "status": "SUCCESS",
        "guardas_status": "SUCCESS",
        "saved_count": 120,
        "duration_seconds": 95.0,
    }
    base.update(extra)
    return base


def _resumo(daily_summary, *payloads):
    """Roda o caminho real: collect_from_logging -> build_html. Devolve so o html."""
    return _assunto_e_resumo(daily_summary, *payloads)[1]


def _assunto_e_resumo(daily_summary, *payloads):
    """Idem, devolvendo (subject, html)."""
    agg = defaultdict(daily_summary.WFAgg)
    daily_summary.collect_from_logging(
        _FakeLogging([_Entry(p) for p in payloads]), INICIO, FIM, agg
    )
    return daily_summary.build_html(DIA, agg)


def test_guarda_vermelha_com_workflow_verde_aparece_no_email(daily_summary):
    """O caso do C4: a guarda falhou, o workflow nao. O e-mail tem que dizer isso."""
    html = _resumo(daily_summary, _payload(guardas_status="PARTIAL_FAILURE"))

    assert "uarda" in html, (
        "guarda vermelha nao aparece em lugar nenhum do e-mail — "
        "workflow verde + guarda vermelha sai 100% verde"
    )
    assert "workflow-futebol-odds" in html


def test_guarda_verde_nao_polui_o_email(daily_summary):
    """Contraparte: dia sem guarda vermelha nao ganha alarme."""
    html = _resumo(daily_summary, _payload())

    assert "PARTIAL_FAILURE" not in html
    assert "tag:guarda" not in html


def test_assunto_denuncia_a_guarda_com_o_workflow_verde(daily_summary):
    """O buraco do C4 em uma linha: [OK] com guarda vermelha e alarme mudo.

    Quem le a caixa de entrada nao abre um e-mail marcado [OK]. Se a guarda vermelha nao
    chega no ASSUNTO, ter a secao no corpo nao resolve nada.
    """
    assunto, _ = _assunto_e_resumo(daily_summary, _payload(guardas_status="PARTIAL_FAILURE"))

    assert not assunto.startswith("[OK]")
    assert "GUARDA" in assunto


def test_assunto_soma_falha_de_workflow_e_guarda(daily_summary):
    """Guarda vermelha nao pode APAGAR a sinalizacao de falha do workflow, nem vice-versa."""
    assunto, _ = _assunto_e_resumo(
        daily_summary, _payload(status="PARTIAL_FAILURE", guardas_status="PARTIAL_FAILURE")
    )

    assert "FALHAS" in assunto
    assert "GUARDA" in assunto


def test_assunto_limpo_quando_tudo_verde(daily_summary):
    assunto, _ = _assunto_e_resumo(daily_summary, _payload())

    assert assunto.startswith("[OK]")


def test_workflow_sem_guardas_nao_vira_falso_positivo(daily_summary):
    """A maioria dos workflows nao roda guarda e nao emite o campo. Ausencia != vermelho."""
    sem_campo = _payload(workflow_name="workflow_futebol_sync")
    sem_campo.pop("guardas_status")
    assunto, html = _assunto_e_resumo(daily_summary, sem_campo)

    assert assunto.startswith("[OK]")
    assert "tag:guarda" not in html


def test_conta_as_execucoes_e_marca_a_ultima(daily_summary):
    """Guarda vermelha persiste por varias execucoes no dia — a secao conta e data."""
    _, html = _assunto_e_resumo(
        daily_summary,
        _payload(guardas_status="PARTIAL_FAILURE"),
        _payload(guardas_status="PARTIAL_FAILURE"),
        _payload(),
    )

    assert "workflow-futebol-odds" in html
    assert ">2<" in html  # duas execucoes com guarda vermelha, nao tres
