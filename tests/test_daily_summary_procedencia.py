"""Deriva de imagem dbt tem que aparecer no resumo diario.

Os modelos dbt rodam de uma imagem pre-buildada, nao do master. Quando alguem mergeia e
esquece de rebuildar, os workflows seguem VERDES rodando codigo velho — e a fase de guardas
nao acusa, porque roda da mesma imagem derivada. O caso discriminante e esse:
**tudo verde no resumo + imagem derivada**. Se a deriva nao subir para o assunto, o e-mail
sai `[OK]` num dia em que producao esta rodando outra coisa.

Cobre tambem a assimetria deliberada de alarme: veredito VERMELHO e detector PARADO viram
`[DERIVA]`; ERRO DE LEITURA nao vira. O detector horario e o alarme primario e continua
vivo — fazer o assunto piscar por falha de leitura deste e-mail treinaria todo mundo a
ignora-lo, que e como esta classe de bug sobreviveu.
"""
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.reporting.procedencia import (
    CarimboJob,
    ProcedenciaInfo,
    VereditoDetector,
    build_procedencia_section,
)

DIA = date(2026, 8, 7)
AGORA = datetime.now(timezone.utc)

_STUBS_GCP = (
    "google.cloud.logging",
    "google.cloud.workflows",
    "google.cloud.workflows.executions_v1",
    "google.cloud.workflows.executions_v1.types",
)


@pytest.fixture
def daily_summary():
    """Mesma fiacao de stubs do test_daily_summary_guardas."""
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


def _carimbos_ok():
    return [
        CarimboJob(job="dbt-futebol", carimbo="a164ae5c" * 5, commit="bbe524e",
                   atualizado_em="2026-08-07T18:52:23Z"),
        CarimboJob(job="dbt-nba", carimbo="1e97acbf" * 5, commit="bbe524e",
                   atualizado_em="2026-08-07T19:10:00Z"),
    ]


def _info(conclusion="success", quando=None, error=None):
    return ProcedenciaInfo(
        carimbos=_carimbos_ok(),
        veredito=VereditoDetector(
            conclusion=conclusion,
            quando=quando if quando is not None else AGORA - timedelta(minutes=20),
            url="https://github.com/tech-lamjav/analytics-engineering/actions/runs/1",
            error=error,
        ),
    )


# ---------------------------------------------------------------- alarme


def test_veredito_vermelho_vira_alarme():
    assert _info(conclusion="failure").alarme is True


def test_veredito_verde_nao_vira_alarme():
    assert _info(conclusion="success").alarme is False


def test_detector_parado_vira_alarme():
    """O vigia caiu: workflow agendado em repo publico morre apos 60d sem atividade."""
    assert _info(quando=AGORA - timedelta(hours=9)).alarme is True


def test_atraso_dentro_da_folga_nao_vira_alarme():
    """Agendamento do GitHub e best-effort e atrasa rotineiramente."""
    assert _info(quando=AGORA - timedelta(hours=2)).alarme is False


def test_erro_de_leitura_nao_vira_alarme():
    """Assimetria deliberada: erro de leitura degrada o corpo, nao pisca o assunto."""
    info = ProcedenciaInfo(
        carimbos=_carimbos_ok(),
        veredito=VereditoDetector(error="ConnectionError: timeout"),
    )
    assert info.alarme is False
    assert "indisponivel" in build_procedencia_section(info)


# ---------------------------------------------------------------- assunto


def test_tudo_verde_mas_derivado_sobe_para_o_assunto(daily_summary):
    """O caso discriminante: nenhum workflow falhou, nenhuma guarda acendeu, e ainda assim
    producao esta rodando codigo velho."""
    agg = defaultdict(daily_summary.WFAgg)
    a = agg["workflow_futebol"]
    a.total, a.success = 5, 5

    subject, _ = daily_summary.build_html(DIA, agg, None, _info(conclusion="failure"))
    assert "[DERIVA]" in subject
    assert "[OK]" not in subject


def test_tokens_justapostos_com_falhas_e_guarda(daily_summary):
    """Convencao do PR #42: tokens justapostos, nao fundidos, p/ filtro casar por substring."""
    agg = defaultdict(daily_summary.WFAgg)
    a = agg["workflow_futebol"]
    a.total, a.failed = 2, 1
    a.guardas_red = [AGORA]

    subject, _ = daily_summary.build_html(DIA, agg, None, _info(conclusion="failure"))
    assert subject.startswith("[FALHAS][GUARDA][DERIVA]")


def test_sem_procedencia_o_email_fica_como_antes(daily_summary):
    """None omite a secao e o token — o e-mail de antes segue intacto."""
    agg = defaultdict(daily_summary.WFAgg)
    agg["workflow_futebol"].total = 1
    agg["workflow_futebol"].success = 1

    subject, html = daily_summary.build_html(DIA, agg, None, None)
    assert subject.startswith("[OK]")
    assert "DERIVA" not in subject
    assert "Procedencia" not in html


# ---------------------------------------------------------------- corpo


def test_job_sem_carimbo_aparece_marcado():
    """Fail-closed: carimbo ausente e o estado de um job nunca deployado pelo script novo."""
    info = ProcedenciaInfo(
        carimbos=[CarimboJob(job="dbt-nba"), *_carimbos_ok()[:1]],
        veredito=VereditoDetector(conclusion="failure", quando=AGORA),
    )
    html = build_procedencia_section(info)
    assert "SEM CARIMBO" in html
    assert "dbt-nba" in html


def test_secao_ausente_quando_nao_ha_leitura():
    assert build_procedencia_section(None) == ""


def test_secao_verde_menciona_o_carimbo():
    html = build_procedencia_section(_info())
    assert "dbt-futebol" in html
    assert "a164ae5c" in html


# ------------------------------------------------- degradacoes vistas ao vivo


def test_sem_projeto_configurado_diz_o_motivo(monkeypatch):
    """`projects/None` na URL faz a API responder 403, que se le como problema de IAM e
    manda o operador cacar permissao. Achado rodando ao vivo — a mensagem tem que nomear
    a causa real."""
    import src.reporting.procedencia as mod

    monkeypatch.setattr(mod, "GCP_PROJECT_ID", None)
    carimbos = mod.collect_carimbos()
    assert len(carimbos) == 2
    assert all("GCP_PROJECT_ID" in (c.error or "") for c in carimbos)


def test_detector_inexistente_e_estado_nomeado_e_nao_alarme(monkeypatch):
    """404 no workflow e ambiguo (nao mergeado ainda vs apagado). Vira estado nomeado, e
    nao alarme, pela mesma regra do erro de leitura."""
    import src.reporting.procedencia as mod

    class _Resp:
        status_code = 404

        def json(self):  # pragma: no cover - nao deve ser chamado
            raise AssertionError("404 nao deveria chegar a ser desserializado")

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp())
    v = mod.collect_veredito()
    assert v.conclusion is None
    assert "nao encontrado" in v.error
    assert ProcedenciaInfo(carimbos=[], veredito=v).alarme is False
