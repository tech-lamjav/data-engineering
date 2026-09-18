"""Log intermediario de erro/recovery nao pode duplicar o incidente sob "unknown".

Cada workflow com recovery (fixtures-live, odds, lineups...) emite, dentro do `except`,
um log com `status` mas sem `workflow_name` nem `duration_seconds` (so o log_completion
terminal leva os dois). Medido em 2026-09-17: as 12 PARTIAL_FAILURE de
workflow-futebol-fixtures-live e as 12 de "unknown" no resumo eram os mesmos 12
incidentes, contados 2x porque collect_from_logging agregava qualquer entrada com
`status`, nao so o log terminal.
"""
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

DIA = date(2026, 9, 17)
INICIO = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)
FIM = datetime(2026, 9, 18, 3, 0, tzinfo=timezone.utc)
QUANDO = datetime(2026, 9, 17, 3, 33, tzinfo=timezone.utc)

_STUBS_GCP = (
    "google.cloud.logging",
    "google.cloud.workflows",
    "google.cloud.workflows.executions_v1",
    "google.cloud.workflows.executions_v1.types",
)


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
    def __init__(self, payload, timestamp=QUANDO):
        self.payload = payload
        self.timestamp = timestamp


class _FakeLogging:
    def __init__(self, entries):
        self._entries = entries

    def list_entries(self, **kwargs):
        return iter(self._entries)


# Log intermediario real, capturado de workflow_futebol_fixtures_live.yml (except do recovery):
# tem `status`, mas nao `workflow_name` nem `duration_seconds`.
LOG_INTERMEDIARIO_RECOVERY = {
    "message": "dbt fixtures rebuild falhou mesmo após --full-refresh",
    "status": "PARTIAL_FAILURE",
}

# Log terminal (log_completion) do mesmo incidente — este e' o unico que deve contar.
LOG_TERMINAL = {
    "message": "Workflow fixtures live completed",
    "workflow_name": "workflow_futebol_fixtures_live",
    "status": "PARTIAL_FAILURE",
    "saved_count": 5,
    "duration_seconds": 184.5,
}


def _agg(daily_summary, *payloads):
    agg = defaultdict(daily_summary.WFAgg)
    count = daily_summary.collect_from_logging(
        _FakeLogging([_Entry(p) for p in payloads]), INICIO, FIM, agg
    )
    return agg, count


def test_log_intermediario_sem_duration_seconds_nao_gera_unknown(daily_summary):
    """O caso do incidente: 1 log intermediario + 1 log terminal = 1 execucao contada."""
    agg, count = _agg(daily_summary, LOG_INTERMEDIARIO_RECOVERY, LOG_TERMINAL)

    assert "unknown" not in agg
    assert agg["workflow-futebol-fixtures-live"].total == 1
    assert agg["workflow-futebol-fixtures-live"].partial == 1
    assert count == 1


def test_log_intermediario_sozinho_nao_aparece_em_lugar_nenhum(daily_summary):
    """Sem o log terminal (ex.: falha dura antes de emitir log_completion), o
    intermediario tambem nao deve virar uma linha "unknown" fantasma."""
    agg, count = _agg(daily_summary, LOG_INTERMEDIARIO_RECOVERY)

    assert len(agg) == 0
    assert count == 0


def test_log_terminal_sem_workflow_name_ainda_cai_em_unknown(daily_summary):
    """Se um workflow REAL esquecer de emitir workflow_name no proprio log_completion
    (tem duration_seconds mas nao workflow_name), isso continua visivel como "unknown" —
    e um sinal real de bug de instrumentacao, nao deve ser escondido."""
    payload_sem_nome = {**LOG_TERMINAL}
    payload_sem_nome.pop("workflow_name")

    agg, count = _agg(daily_summary, payload_sem_nome)

    assert agg["unknown"].total == 1
    assert count == 1


def test_filtro_de_query_restringe_ao_log_terminal(daily_summary):
    """Defesa em profundidade tambem no filtro enviado ao Cloud Logging — reduz volume
    trafegado, mesmo que o corte real de corretude esteja em collect_from_logging."""
    flt = daily_summary._logging_filter(INICIO, FIM)

    assert "jsonPayload.duration_seconds:*" in flt
