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
    Avaliacao,
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
# Falha de medição não pode calar o detector
# --------------------------------------------------------------------------------------
def test_falha_ao_medir_uma_tabela_vira_vermelho_e_nao_excecao():
    """Tabela some do BQ: tem que APARECER, não derrubar a medição das outras 21."""
    a = Atraso("fact_sumida", None, None, timedelta(0), erro="NotFound")
    assert a.vermelho


# --------------------------------------------------------------------------------------
# O e-mail é montado das TRANSIÇÕES, não do vermelho corrente
# --------------------------------------------------------------------------------------
def _av(env, motivo, horas_atraso=None, desde_h=None):
    atrasos = []
    if horas_atraso is not None:
        bq = AGORA - timedelta(hours=horas_atraso)
        atrasos.append(calcula_atraso("int_futebol_premissas_ou", bq, None, AGORA))
    else:
        bq = AGORA - timedelta(minutes=10)
        atrasos.append(calcula_atraso("int_futebol_premissas_ou", bq, bq, AGORA))
    desde = AGORA - timedelta(hours=desde_h) if desde_h else None
    return Avaliacao(env, atrasos, motivo, desde)


def test_recuperacao_de_um_ambiente_nao_some_com_o_outro_vermelho():
    """O caso que a revisão pegou.

    PRD volta enquanto o DEV segue caído. Montar o e-mail a partir do vermelho corrente
    produzia um assunto só de ATRASO, e como o estado do PRD é gravado como verde de
    qualquer jeito, a transição era CONSUMIDA — ninguém jamais saberia que o PRD voltou.
    """
    avaliacoes = [
        _av("prd", "recuperou", horas_atraso=None, desde_h=40),
        _av("dev", None, horas_atraso=40),
    ]
    subject, html = monta_email(avaliacoes, AGORA)
    assert "RECUPERADO" in subject
    assert "prd" in subject
    assert "recuperado" in html
    assert "40h00m" in html  # a duração do episódio que o ADR promete


def test_ciclo_misto_diz_as_duas_coisas():
    avaliacoes = [
        _av("prd", "recuperou", horas_atraso=None, desde_h=10),
        _av("dev", "abriu", horas_atraso=6),
    ]
    subject, _ = monta_email(avaliacoes, AGORA)
    assert subject.startswith("[ATRASO]")
    assert "dev" in subject
    assert "prd recuperado" in subject


def test_abriu_domina_lembrete_no_token_do_assunto():
    avaliacoes = [_av("prd", "abriu", horas_atraso=4), _av("dev", "lembrete", horas_atraso=40)]
    subject, _ = monta_email(avaliacoes, AGORA)
    assert subject.startswith("[ATRASO]")
    assert "LEMBRETE" not in subject


def test_lembrete_puro_se_distingue():
    avaliacoes = [_av("prd", "lembrete", horas_atraso=40)]
    subject, _ = monta_email(avaliacoes, AGORA)
    assert "LEMBRETE" in subject


def test_ambiente_sem_transicao_nao_entra_no_corpo():
    avaliacoes = [_av("prd", "abriu", horas_atraso=6), _av("dev", None, horas_atraso=40)]
    _, html = monta_email(avaliacoes, AGORA)
    assert "<h3" in html
    assert html.count("<h3") == 1  # só o prd


def test_sem_transicao_nenhuma_nao_monta_email():
    import pytest

    with pytest.raises(ValueError):
        monta_email([_av("prd", None, horas_atraso=40)], AGORA)


def test_tabela_com_erro_aparece_com_a_situacao():
    avaliacao = Avaliacao(
        "prd", [Atraso("fact_sumida", None, None, timedelta(0), erro="NotFound")], "abriu", None
    )
    _, html = monta_email([avaliacao], AGORA)
    assert "fact_sumida" in html
    assert "falha ao medir" in html


def test_dataclass_atraso_e_imutavel():
    a = Atraso("t", AGORA, AGORA, timedelta(0))
    try:
        a.tabela = "outra"
    except Exception:
        return
    raise AssertionError("Atraso deveria ser frozen")
