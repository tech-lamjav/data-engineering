"""Detector de atraso do sync: o sinal e a política de silêncio.

Dois casos discriminantes governam este arquivo.

1. **Tabela de cadência semanal com o sync saudável.** É o caso que derruba a alternativa
   óbvia ("sincronizada há mais de N horas"): a `fact_team_season_stats` roda 1×/semana e
   ficaria vermelha 6 dias em 7. Medindo ATRASO — BQ à frente do Postgres — ela fica verde
   sem limiar próprio, porque não há nada pendente.

2. **Vermelho que persiste.** Um alarme que fala a cada ciclo enquanto o problema dura é
   o que transformou o `[FALHAS]` diário em papel de parede durante o incidente de
   26–29/08/2026. A política é falar na transição e depois calar, com um lembrete a cada
   24h.
"""
from datetime import datetime, timedelta, timezone

from src.monitoring.atraso_sync import (
    INTERVALO_LEMBRETE,
    LIMIAR_ATRASO,
    Atraso,
    EstadoDetector,
    calcula_atraso,
    decide_envio,
    monta_email,
)

AGORA = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)


def _verde():
    return EstadoDetector("verde", None, None)


def _vermelho(desde_h=5, ultimo_aviso_h=5):
    return EstadoDetector(
        "vermelho",
        AGORA - timedelta(hours=desde_h),
        AGORA - timedelta(hours=ultimo_aviso_h),
    )


# --------------------------------------------------------------------------------------
# O sinal
# --------------------------------------------------------------------------------------
def test_tabela_semanal_com_sync_saudavel_nao_acende():
    """Caso 1: BQ parado há 5 dias, Postgres em dia. Atraso zero, não idade de 5 dias."""
    bq_modified = AGORA - timedelta(days=5)
    a = calcula_atraso("fact_team_season_stats", bq_modified, bq_modified, AGORA)
    assert a.atraso == timedelta(0)
    assert not a.vermelho


def test_postgres_atras_do_bq_mede_desde_o_carimbo_do_bq():
    """O atraso conta do momento em que o dado novo passou a existir, não de agora."""
    bq_modified = AGORA - timedelta(hours=7)
    sincronizado = AGORA - timedelta(hours=30)
    a = calcula_atraso("int_futebol_premissas_ou", bq_modified, sincronizado, AGORA)
    assert a.atraso == timedelta(hours=7)
    assert a.vermelho


def test_tabela_nunca_sincronizada_conta_como_atrasada():
    a = calcula_atraso("fact_nova", AGORA - timedelta(hours=9), None, AGORA)
    assert a.atraso == timedelta(hours=9)
    assert a.vermelho


def test_limiar_e_exclusivo_abaixo_dele():
    quase = calcula_atraso("t", AGORA - LIMIAR_ATRASO + timedelta(minutes=1), None, AGORA)
    exato = calcula_atraso("t", AGORA - LIMIAR_ATRASO, None, AGORA)
    assert not quase.vermelho
    assert exato.vermelho


def test_postgres_a_frente_do_bq_nao_e_atraso():
    """Acontece logo após um sync: o carimbo gravado pode ser >= o lido agora."""
    bq_modified = AGORA - timedelta(hours=10)
    a = calcula_atraso("t", bq_modified, AGORA - timedelta(minutes=1), AGORA)
    assert a.atraso == timedelta(0)


# --------------------------------------------------------------------------------------
# A política de silêncio
# --------------------------------------------------------------------------------------
def test_abre_episodio_manda_e_marca_desde():
    motivo, novo = decide_envio(_verde(), ha_vermelho=True, agora=AGORA)
    assert motivo == "abriu"
    assert novo.estado == "vermelho"
    assert novo.desde == AGORA


def test_vermelho_recente_fica_calado():
    """O caso que impede o dilúvio: episódio aberto e já avisado há 5h."""
    motivo, novo = decide_envio(_vermelho(), ha_vermelho=True, agora=AGORA)
    assert motivo is None
    assert novo.estado == "vermelho"


def test_vermelho_preserva_o_inicio_do_episodio():
    anterior = _vermelho(desde_h=50, ultimo_aviso_h=5)
    _, novo = decide_envio(anterior, ha_vermelho=True, agora=AGORA)
    assert novo.desde == anterior.desde


def test_lembrete_sai_depois_de_24h():
    anterior = _vermelho(desde_h=50, ultimo_aviso_h=INTERVALO_LEMBRETE.total_seconds() / 3600)
    motivo, novo = decide_envio(anterior, ha_vermelho=True, agora=AGORA)
    assert motivo == "lembrete"
    assert novo.ultimo_aviso_em == AGORA
    assert novo.desde == anterior.desde


def test_recuperacao_manda():
    motivo, novo = decide_envio(_vermelho(), ha_vermelho=False, agora=AGORA)
    assert motivo == "recuperou"
    assert novo.estado == "verde"


def test_verde_continuado_fica_calado():
    motivo, novo = decide_envio(_verde(), ha_vermelho=False, agora=AGORA)
    assert motivo is None
    assert novo.estado == "verde"


# --------------------------------------------------------------------------------------
# O e-mail
# --------------------------------------------------------------------------------------
def _atraso(tabela, horas, env_sincronizado=True):
    bq = AGORA - timedelta(hours=horas)
    return calcula_atraso(tabela, bq, AGORA - timedelta(days=3) if env_sincronizado else None, AGORA)


def test_assunto_carrega_ambiente_e_duracao():
    por_ambiente = {
        "prd": [_atraso("int_futebol_premissas_ou", 5)],
        "dev": [_atraso("int_futebol_premissas_ou", 5)],
    }
    subject, html = monta_email(por_ambiente, {"prd": "abriu"}, AGORA)
    assert "[ATRASO]" in subject
    assert "dev, prd" in subject
    assert "5h00m" in subject
    assert "int_futebol_premissas_ou" in html


def test_assunto_de_lembrete_se_distingue():
    por_ambiente = {"prd": [_atraso("t", 30)]}
    subject, _ = monta_email(por_ambiente, {"prd": "lembrete"}, AGORA)
    assert "LEMBRETE" in subject


def test_recuperacao_nao_lista_tabela():
    por_ambiente = {"prd": [_atraso("t", 1)], "dev": [_atraso("t", 1)]}
    subject, html = monta_email(por_ambiente, {"prd": "recuperou"}, AGORA)
    assert subject.startswith("[RECUPERADO]")
    assert "<table" not in html


def test_tabela_verde_nao_aparece_no_corpo():
    por_ambiente = {"prd": [_atraso("atrasada", 6), _atraso("em_dia", 1)]}
    _, html = monta_email(por_ambiente, {"prd": "abriu"}, AGORA)
    assert "atrasada" in html
    assert "em_dia" not in html


def test_dataclass_atraso_e_imutavel():
    a = Atraso("t", AGORA, AGORA, timedelta(0))
    try:
        a.tabela = "outra"
    except Exception:
        return
    raise AssertionError("Atraso deveria ser frozen")
