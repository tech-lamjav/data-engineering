"""Testes da secao de cota da API-Football no resumo diario.

Cobre a leitura do /status, os dois limiares de alerta (consumo e vencimento) e a
formatacao da secao HTML. Infra (API) e mockada — nenhum acesso de rede, no molde de
tests/test_bq_to_postgres_format.py.

Importa SO src.reporting.api_quota: src.reporting.daily_summary depende de
google-cloud-logging/workflows, que vivem no requirements do Cloud Run e nao no de
teste. Por isso a leitura, os limiares e a renderizacao moram em api_quota — o
daily_summary so faz a fiacao.
"""
from datetime import date, datetime, timezone

import pytest

from src.config import QUOTA_ALERT_PCT, SUBSCRIPTION_ALERT_DAYS
from src.reporting.api_quota import (
    QuotaInfo,
    build_quota_section,
    collect_quota,
    parse_quota,
)

# Dia do relatorio e horario da leitura (00:05 BRT = 03:05 UTC, quando o resumo roda).
DIA = date(2026, 8, 6)
LEITURA = datetime(2026, 8, 6, 3, 5, tzinfo=timezone.utc)


def _envelope(current=4623, limit_day=7500, end="2026-08-11T12:21:59+00:00", active=True):
    """Envelope real do /status (sondado em 2026-08-06)."""
    return {
        "get": "status",
        "parameters": [],
        "errors": [],
        "results": 0,
        "paging": {"current": 1, "total": 1},
        "response": {
            "account": {"firstname": "X", "lastname": "Y", "email": "z@z"},
            "subscription": {"plan": "Pro", "end": end, "active": active},
            "requests": {"current": current, "limit_day": limit_day},
        },
    }


# --------------------------------------------------------------------------- #
# Leitura do /status
# --------------------------------------------------------------------------- #
def test_parse_le_consumo_limite_plano_e_vencimento():
    q = parse_quota(_envelope(), read_at=LEITURA)

    assert q.error is None
    assert q.current == 4623
    assert q.limit_day == 7500
    assert q.plan == "Pro"
    assert q.subscription_end == date(2026, 8, 11)
    assert q.active is True
    assert q.read_at == LEITURA


def test_parse_calcula_o_percentual_de_consumo():
    q = parse_quota(_envelope(current=7189, limit_day=7500))
    assert q.pct == pytest.approx(95.85, abs=0.01)


def test_parse_errors_da_api_degrada_a_leitura():
    # Cota estourada chega como HTTP 200 + errors preenchido (nao 429).
    env = _envelope()
    env["errors"] = {"requests": "You have reached the request limit for the day"}

    q = parse_quota(env)

    assert q.current is None
    assert "request limit" in q.error


def test_parse_response_sem_requests_degrada():
    q = parse_quota({"errors": [], "response": {"subscription": {"plan": "Pro"}}})

    assert q.current is None
    assert q.error is not None


def test_parse_response_em_lista_degrada():
    # A API devolve `response` como lista em varios endpoints; no /status e dict.
    q = parse_quota({"errors": [], "response": []})

    assert q.current is None
    assert q.error is not None


def test_parse_aceita_vencimento_em_data_simples():
    q = parse_quota(_envelope(end="2026-08-11"))
    assert q.subscription_end == date(2026, 8, 11)


def test_parse_vencimento_ilegivel_nao_derruba_a_leitura():
    q = parse_quota(_envelope(end="daqui a pouco"))

    assert q.subscription_end is None
    assert q.current == 4623  # o resto da leitura sobrevive


def test_pct_sem_limite_nao_divide_por_zero():
    assert parse_quota(_envelope(limit_day=0)).pct is None


# --------------------------------------------------------------------------- #
# Limiares de alerta
# --------------------------------------------------------------------------- #
def test_consumo_acima_do_limiar_alerta():
    q = parse_quota(_envelope(current=7189, limit_day=7500))  # 95,9%
    assert q.quota_alert() is True


def test_consumo_exatamente_no_limiar_nao_alerta():
    # "passa de 80%" e estrito: 80,0% cravado nao alerta.
    q = parse_quota(_envelope(current=6000, limit_day=7500))
    assert q.pct == QUOTA_ALERT_PCT
    assert q.quota_alert() is False


def test_vencimento_dentro_do_limiar_alerta():
    q = parse_quota(_envelope(end="2026-08-11T12:21:59+00:00"))
    assert q.days_to_end(DIA) == 5
    assert q.subscription_alert(DIA) is True


def test_vencimento_exatamente_no_limiar_nao_alerta():
    # "faltam menos de 14 dias" e estrito: 14 dias cravados nao alerta.
    q = parse_quota(_envelope(end="2026-08-20"))
    assert q.days_to_end(DIA) == SUBSCRIPTION_ALERT_DAYS
    assert q.subscription_alert(DIA) is False


def test_plano_ja_vencido_alerta():
    q = parse_quota(_envelope(end="2026-08-01"))
    assert q.days_to_end(DIA) == -5
    assert q.subscription_alert(DIA) is True


def test_plano_inativo_alerta_mesmo_com_vencimento_longe():
    q = parse_quota(_envelope(end="2027-01-01", active=False))
    assert q.subscription_alert(DIA) is True


def test_leitura_degradada_nao_alerta():
    # Sem numero nao ha alerta — a secao degradada ja diz o que houve.
    q = QuotaInfo(error="timeout")
    assert q.quota_alert() is False
    assert q.subscription_alert(DIA) is False


# --------------------------------------------------------------------------- #
# Formatacao da secao
# --------------------------------------------------------------------------- #
def test_secao_mostra_consumo_limite_e_percentual():
    html = build_quota_section(parse_quota(_envelope(), read_at=LEITURA), DIA)

    assert "Cota da API-Football" in html
    assert "4623" in html and "7500" in html
    assert "61.6%" in html


def test_secao_mostra_plano_e_data_de_vencimento():
    html = build_quota_section(parse_quota(_envelope(), read_at=LEITURA), DIA)

    assert "Pro" in html
    assert "2026-08-11" in html
    assert "5 dias" in html


def test_secao_carimba_o_horario_da_leitura():
    # O contador do /status e do dia CORRENTE da API e a leitura acontece as 00:05 BRT:
    # sem o carimbo, um numero parcial se parece com o consumo do dia inteiro.
    html = build_quota_section(parse_quota(_envelope(), read_at=LEITURA), DIA)
    assert "00:05" in html


def test_secao_destaca_consumo_acima_do_limiar():
    q = parse_quota(_envelope(current=7189, limit_day=7500), read_at=LEITURA)
    html = build_quota_section(q, DIA)

    assert "ALERTA" in html
    assert "95.9%" in html


def test_secao_destaca_vencimento_proximo():
    q = parse_quota(_envelope(current=100, end="2026-08-11T12:21:59+00:00"), read_at=LEITURA)
    html = build_quota_section(q, DIA)

    assert "ALERTA" in html
    assert "2026-08-11" in html


def test_secao_sem_alerta_quando_tudo_esta_folgado():
    q = parse_quota(_envelope(current=100, limit_day=7500, end="2027-01-01"), read_at=LEITURA)
    html = build_quota_section(q, DIA)

    assert "ALERTA" not in html
    assert "Cota da API-Football" in html


def test_secao_degradada_renderiza_com_o_motivo():
    # AC: falha ao consultar o status nao derruba o resumo — a secao sai degradada.
    html = build_quota_section(QuotaInfo(error="ConnectionError: timeout"), DIA)

    assert "Cota da API-Football" in html
    assert "ConnectionError: timeout" in html


def test_secao_escapa_html_do_motivo():
    html = build_quota_section(QuotaInfo(error="<script>alert(1)</script>"), DIA)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_secao_vazia_quando_nao_houve_leitura():
    assert build_quota_section(None, DIA) == ""


# --------------------------------------------------------------------------- #
# Coleta (nunca levanta)
# --------------------------------------------------------------------------- #
class _ClienteFake:
    def __init__(self, envelope=None, erro=None):
        self._envelope = envelope
        self._erro = erro
        self.chamadas = 0

    def get_status(self):
        self.chamadas += 1
        if self._erro:
            raise self._erro
        return self._envelope


def test_collect_quota_le_o_status_do_cliente_uma_vez():
    cliente = _ClienteFake(envelope=_envelope())

    q = collect_quota(client=cliente)

    assert cliente.chamadas == 1  # 1 chamada/dia
    assert q.current == 4623


def test_collect_quota_carimba_o_horario_da_leitura():
    q = collect_quota(client=_ClienteFake(envelope=_envelope()))

    assert q.read_at is not None
    assert q.read_at.tzinfo is not None


def test_collect_quota_nao_levanta_quando_o_cliente_falha():
    # O resumo e o unico canal de alarme do pipeline: nao pode cair porque a cota
    # nao pode ser lida.
    q = collect_quota(client=_ClienteFake(erro=RuntimeError("boom")))

    assert q.current is None
    assert "RuntimeError" in q.error
    assert "boom" in q.error


def test_collect_quota_nao_levanta_quando_o_cliente_nem_constroi(monkeypatch):
    # Cenario real: servico sem API_FOOTBALL_KEY montado (secret nao ligado no deploy).
    def _explode():
        raise RuntimeError("API_FOOTBALL_KEY ausente")

    monkeypatch.setattr("src.reporting.api_quota.ApiFootballClient", _explode)

    q = collect_quota()

    assert q.current is None
    assert "API_FOOTBALL_KEY" in q.error


# --------------------------------------------------------------------------- #
# Data de referencia do countdown
# --------------------------------------------------------------------------- #
def test_countdown_conta_do_dia_da_leitura_e_nao_do_dia_do_relatorio():
    # O relatorio cobre o dia FECHADO (anterior), mas o email sai no dia da leitura.
    # Contar pelo dia do relatorio exibiria um dia a mais de prazo e atrasaria o alerta
    # em um dia — os dois na direcao errada.
    q = parse_quota(_envelope(end="2026-08-19"), read_at=LEITURA)  # leitura em 06/08
    dia_do_relatorio = date(2026, 8, 5)

    assert q.reference_date(dia_do_relatorio) == date(2026, 8, 6)
    assert q.days_to_end(dia_do_relatorio) == 13  # pelo dia do relatorio seriam 14
    assert q.subscription_alert(dia_do_relatorio) is True  # 14 nao alertaria


def test_countdown_cai_para_o_dia_do_relatorio_sem_carimbo_de_leitura():
    q = parse_quota(_envelope(end="2026-08-19"))  # read_at=None

    assert q.reference_date(DIA) == DIA
    assert q.days_to_end(DIA) == 13


# --------------------------------------------------------------------------- #
# Forma compacta p/ o Cloud Logging
# --------------------------------------------------------------------------- #
def test_as_log_dict_resume_a_leitura():
    q = parse_quota(_envelope(current=7189, limit_day=7500), read_at=LEITURA)

    d = q.as_log_dict(DIA)

    assert d["current"] == 7189
    assert d["limit_day"] == 7500
    assert d["pct"] == 95.9
    assert d["subscription_end"] == "2026-08-11"
    assert d["days_to_end"] == 5
    assert d["alert_quota"] is True
    assert d["alert_subscription"] is True
    assert d["error"] is None


def test_as_log_dict_sobrevive_a_leitura_degradada():
    d = QuotaInfo(error="timeout").as_log_dict(DIA)

    assert d["current"] is None
    assert d["pct"] is None
    assert d["days_to_end"] is None
    assert d["error"] == "timeout"
