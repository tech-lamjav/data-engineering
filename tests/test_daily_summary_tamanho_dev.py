"""Tamanho do DEV no resumo diário (DE#106, histórias 11-17).

Duas camadas, como nas seções de cota e de guardas:
- a lógica (última leitura do dia, limiar de 450 MB, seção HTML, estado degradado) em
  `src.reporting.tamanho_dev`, testada sem stub de SDK nenhum;
- a fiação em `daily_summary` (leitura do log de conclusão do `workflow_futebol_sync`,
  token `[DEV]` no assunto), com o SDK do Google stubado e o fake de Cloud Logging.

Regra herdada de `guardas_status`/`quota_remaining`: chave AUSENTE (ou nula) no log de
conclusão não é vermelho nem leitura, é "esse workflow não mede isso".
"""
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.reporting.tamanho_dev import (
    DEV_ALERTA_MB,
    DEV_TETO_MB,
    DevSizeInfo,
    build_dev_size_section,
    collect_dev_size,
)

DIA = date(2026, 10, 1)
H = lambda h, m=0: datetime(2026, 10, 1, h, m, tzinfo=timezone.utc)  # noqa: E731

_STUBS_GCP = (
    "google.cloud.logging",
    "google.cloud.workflows",
    "google.cloud.workflows.executions_v1",
    "google.cloud.workflows.executions_v1.types",
)


# ------------------------------------------------------------------
# Lógica pura
# ------------------------------------------------------------------
def test_os_limiares_sao_os_da_spec():
    assert DEV_TETO_MB == 500 and DEV_ALERTA_MB == 450


def test_com_varias_leituras_no_dia_vale_a_ultima():
    info = collect_dev_size([(H(14), 430.0), (H(22), 470.2), (H(9), 380.0)])

    assert info.reading.size_mb == 470.2
    assert info.reading.read_at == H(22)


def test_leitura_sem_horario_nao_ganha_de_leitura_com_horario():
    info = collect_dev_size([(None, 999.0), (H(10), 400.0)])

    assert info.reading.size_mb == 400.0


def test_sem_leituras_o_estado_e_sem_leitura_e_nao_levanta():
    assert collect_dev_size([]).reading is None


def test_alarme_e_estrito_450_cravado_nao_alerta():
    assert collect_dev_size([(H(10), 450.0)]).alarme is False
    assert collect_dev_size([(H(10), 450.1)]).alarme is True


def test_sem_leitura_nao_e_alarme():
    assert DevSizeInfo(reading=None).alarme is False


def test_secao_abaixo_do_limiar_mostra_mb_e_percentual_sem_alerta():
    html = build_dev_size_section(collect_dev_size([(H(22), 380.0)]))

    assert "380.0 MB" in html
    assert "76.0%" in html  # do teto de 500 MB
    assert "ALERTA" not in html


def test_secao_acima_do_limiar_traz_alerta_com_o_numero_e_o_teto():
    html = build_dev_size_section(collect_dev_size([(H(22), 462.3)]))

    assert "ALERTA" in html
    assert "462.3" in html and "450" in html and "500" in html


def test_secao_sem_leitura_e_degradada_em_ambar_e_nao_e_alerta_nem_silencio():
    html = build_dev_size_section(collect_dev_size([]))

    assert "Tamanho do DEV" in html  # não some: silêncio seria "está bem"
    assert "ALERTA" not in html
    assert "#9a6700" in html  # âmbar
    assert "nao medido" in html.lower() or "não medido" in html.lower()


def test_sem_estado_nenhum_a_secao_e_omitida():
    """build_html chamado sem o argumento mantém o e-mail de antes."""
    assert build_dev_size_section(None) == ""


def test_leitura_nao_finita_e_descartada_e_vale_a_ultima_valida():
    info = collect_dev_size([(H(22), float("nan")), (H(23), 410.0)])

    assert "410.0 MB" in build_dev_size_section(info)  # NaN descartado, vale a válida


# ------------------------------------------------------------------
# Fiação em daily_summary
# ------------------------------------------------------------------
@pytest.fixture
def daily_summary():
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
    def __init__(self, payload, timestamp):
        self.payload = payload
        self.timestamp = timestamp


class _FakeLogging:
    def __init__(self, entries):
        self._entries = entries

    def list_entries(self, **kwargs):
        return iter(self._entries)


def _conclusao(**extra):
    """log_completion do workflow_futebol_sync (o único que leva duration_seconds)."""
    base = {
        "message": "Workflow futebol-sync concluído",
        "workflow_name": "workflow_futebol_sync",
        "status": "SUCCESS",
        "duration_seconds": 640.0,
        "failed_services": [],
        "failed_count": 0,
    }
    base.update(extra)
    return base


def _agg(daily_summary, *pares):
    """pares de (payload, timestamp) -> agg preenchido pelo caminho real de coleta."""
    agg = defaultdict(daily_summary.WFAgg)
    daily_summary.collect_from_logging(
        _FakeLogging([_Entry(p, t) for p, t in pares]), H(3), datetime(2026, 10, 2, 3, tzinfo=timezone.utc), agg
    )
    return agg


def _leituras(daily_summary, agg):
    return [r for a in agg.values() for r in a.dev_size_readings]


def test_log_de_conclusao_com_o_campo_gera_leitura(daily_summary):
    agg = _agg(daily_summary, (_conclusao(dev_size_mb=412.3), H(14)))

    assert _leituras(daily_summary, agg) == [(H(14), 412.3)]


def test_campo_ausente_nao_e_leitura_nem_vermelho(daily_summary):
    agg = _agg(daily_summary, (_conclusao(), H(14)))

    assert _leituras(daily_summary, agg) == []
    assert agg["workflow-futebol-sync"].success == 1


def test_campo_nulo_nao_e_leitura(daily_summary):
    """O workflow nasce com dev_size_mb nulo e o emite assim quando o DEV não mediu."""
    agg = _agg(daily_summary, (_conclusao(dev_size_mb=None), H(14)))

    assert _leituras(daily_summary, agg) == []


def test_campo_ilegivel_e_ignorado_sem_derrubar_a_coleta(daily_summary):
    agg = _agg(
        daily_summary,
        (_conclusao(dev_size_mb="abc"), H(13)),
        (_conclusao(dev_size_mb=401.0), H(14)),
    )

    assert _leituras(daily_summary, agg) == [(H(14), 401.0)]


def test_acima_de_450_o_assunto_ganha_o_token_dev_e_o_corpo_o_alerta(daily_summary):
    agg = _agg(daily_summary, (_conclusao(dev_size_mb=462.3), H(14)))
    info = collect_dev_size(_leituras(daily_summary, agg))

    assunto, html = daily_summary.build_html(DIA, agg, dev_size=info)

    assert "[DEV]" in assunto
    assert "ALERTA" in html and "462.3" in html


def test_abaixo_do_limiar_nao_ha_token_nem_alerta_mas_a_linha_aparece(daily_summary):
    agg = _agg(daily_summary, (_conclusao(dev_size_mb=380.0), H(14)))
    info = collect_dev_size(_leituras(daily_summary, agg))

    assunto, html = daily_summary.build_html(DIA, agg, dev_size=info)

    assert "[DEV]" not in assunto and assunto.startswith("[OK]")
    assert "380.0 MB" in html and "ALERTA" not in html


def test_sem_leitura_no_dia_secao_degradada_sem_token_no_assunto(daily_summary):
    agg = _agg(daily_summary, (_conclusao(), H(14)))
    info = collect_dev_size(_leituras(daily_summary, agg))

    assunto, html = daily_summary.build_html(DIA, agg, dev_size=info)

    assert "[DEV]" not in assunto
    assert "Tamanho do DEV" in html and "ALERTA" not in html


def test_token_dev_e_justaposto_aos_outros_e_nao_fundido(daily_summary):
    """Filtro de caixa de entrada casa por substring: '[FALHAS][DEV]', nunca '[FALHAS+DEV]'."""
    agg = _agg(
        daily_summary,
        (_conclusao(status="PARTIAL_FAILURE", failed_services=["x"], failed_count=1,
                    dev_size_mb=470.0), H(14)),
    )
    info = collect_dev_size(_leituras(daily_summary, agg))

    assunto, _ = daily_summary.build_html(DIA, agg, dev_size=info)

    assert "[FALHAS][DEV]" in assunto


def test_build_html_sem_o_argumento_mantem_o_email_de_antes(daily_summary):
    _, html = daily_summary.build_html(DIA, {})

    assert "Tamanho do DEV" not in html


def test_a_secao_entra_antes_do_fechamento_do_container(daily_summary):
    info = collect_dev_size([(H(22), 380.0)])

    _, html = daily_summary.build_html(DIA, {}, dev_size=info)

    assert html.endswith("</div>")
    assert html.index("Tamanho do DEV") < html.rindex("</div>")
