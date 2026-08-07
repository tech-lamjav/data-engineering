"""Testes do poll pré-jogo de odds (OddsExtractor).

Cobre a janela DIÁRIA nova (horizonte de 7 dias, 1 captura por fixture por dia,
date-stampada) e a disjunção das bandas — que é requisito, não detalhe: bandas
sobrepostas fazem a mesma passada bucketar o mesmo fixture duas vezes, gastando duas
chamadas e gravando duas linhas com rótulos diferentes para o mesmo preço.

Infra (GCS/API) é mockada — nenhum acesso de rede, no idioma de
tests/test_per_fixture_extractor.py.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.config import (
    BRASILEIRAO_ID,
    FUTEBOL_ODDS_HORIZON_MIN,
    FUTEBOL_ODDS_WINDOWS,
    FUTEBOL_ODDS_WINDOWS_DIARIAS,
)


@pytest.fixture
def ext():
    # Patches só p/ o __init__ não instanciar GCSStorage()/ApiFootballClient() reais.
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.odds_extractor.ApiFootballClient"), \
         patch("src.extractors.odds_extractor.time.sleep", lambda *_: None):
        from src.extractors.odds_extractor import OddsExtractor

        e = OddsExtractor()
        e.storage = MagicMock()
        e.client = MagicMock()
        e.storage.bucket.blob.return_value.exists.return_value = False
        e.storage.upload_json.side_effect = lambda **kw: (
            f"gs://b/odds/{kw['game_id']}_{kw['mode']}_{kw.get('date')}.json"
        )
        yield e


def _fixture(horas_ate_o_apito, fixture_id=999, league_id=BRASILEIRAO_ID):
    kickoff = datetime.now(timezone.utc) + timedelta(hours=horas_ate_o_apito)
    return {
        "fixture_id": fixture_id,
        "league_id": league_id,
        "season": 2026,
        "kickoff_ts": int(kickoff.timestamp()),
    }


def _envelope_com_odds():
    return {
        "errors": [],
        "response": [
            {
                "update": "2026-08-07T00:00:00+00:00",
                "bookmakers": [
                    {"id": 4, "name": "Pinnacle", "bets": [{"id": 1, "name": "Match Winner"}]}
                ],
            }
        ],
    }


# --------------------------------------------------------------------------- #
# As bandas
# --------------------------------------------------------------------------- #
def test_o_horizonte_e_um_parametro():
    # AC: "ampliar de 7 para N dias é mudar um número, não acrescentar uma banda nova".
    _, teto = FUTEBOL_ODDS_WINDOWS["daily"]
    assert teto == FUTEBOL_ODDS_HORIZON_MIN
    assert FUTEBOL_ODDS_HORIZON_MIN > 24 * 60  # cobre alem do dia seguinte


def test_as_bandas_sao_disjuntas():
    # O teste que protege a disjuncao no nivel da configuracao: qualquer sobreposicao
    # futura quebra aqui, nao em producao com chamada dobrada.
    bandas = sorted(FUTEBOL_ODDS_WINDOWS.values())
    for (lo_a, hi_a), (lo_b, hi_b) in zip(bandas, bandas[1:]):
        assert hi_a < lo_b, f"bandas se sobrepoem: ({lo_a},{hi_a}) e ({lo_b},{hi_b})"


def test_a_janela_diaria_comeca_imediatamente_acima_da_de_24h():
    _, teto_t24h = FUTEBOL_ODDS_WINDOWS["t24h"]
    piso_daily, _ = FUTEBOL_ODDS_WINDOWS["daily"]
    assert piso_daily == teto_t24h + 1


def test_as_bandas_de_fechamento_ficam_intactas():
    # AC: "t24h, t1h e t15m intactas em posição e semântica".
    assert FUTEBOL_ODDS_WINDOWS["t24h"] == (1320, 1440)
    assert FUTEBOL_ODDS_WINDOWS["t1h"] == (30, 60)
    assert FUTEBOL_ODDS_WINDOWS["t15m"] == (0, 15)


def test_as_janelas_diarias_sao_um_subconjunto_das_janelas():
    # Invariante, nao o valor: uma janela declarada como diaria mas ausente do mapa
    # seria date-stamp em janela que nao existe.
    assert FUTEBOL_ODDS_WINDOWS_DIARIAS <= set(FUTEBOL_ODDS_WINDOWS)
    assert FUTEBOL_ODDS_WINDOWS_DIARIAS  # e ha pelo menos uma


@pytest.mark.parametrize(
    "horas,janela_esperada",
    [
        (0.1, "t15m"),
        (0.2, "t15m"),
        (0.75, "t1h"),
        (1.0, "t1h"),
        (22.5, "t24h"),
        (23.9, "t24h"),
        (48, "daily"),
        (72, "daily"),
        (167, "daily"),
        (12, None),   # vao intencional entre t1h e t24h
        (200, None),  # alem do horizonte
    ],
)
def test_cada_lead_cai_na_janela_certa(ext, horas, janela_esperada):
    # Mais forte que "no maximo uma janela": pina QUAL, entao capturar zero vezes
    # tambem quebra.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(horas)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    ext.extract_and_save()

    if janela_esperada is None:
        ext.storage.upload_json.assert_not_called()
    else:
        assert ext.storage.upload_json.call_count == 1
        assert ext.storage.upload_json.call_args.kwargs["mode"] == janela_esperada


# --------------------------------------------------------------------------- #
# Em que janela cada fixture cai
# --------------------------------------------------------------------------- #
def test_fixture_a_poucos_dias_cai_na_janela_diaria(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    paths = ext.extract_and_save()

    assert len(paths) == 1
    kw = ext.storage.upload_json.call_args.kwargs
    assert kw["mode"] == "daily"
    assert kw["data"]["collection_window"] == "daily"


def test_fixture_perto_do_apito_continua_na_banda_de_24h(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(23)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    ext.extract_and_save()

    kw = ext.storage.upload_json.call_args.kwargs
    assert kw["mode"] == "t24h"


def test_fixture_no_limite_do_horizonte_ainda_entra(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [
        _fixture(FUTEBOL_ODDS_HORIZON_MIN / 60 - 1)
    ]
    ext.client.get_odds.return_value = _envelope_com_odds()

    assert len(ext.extract_and_save()) == 1


def test_fixture_alem_do_horizonte_nao_gasta_chamada(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [
        _fixture(FUTEBOL_ODDS_HORIZON_MIN / 60 + 24)
    ]

    assert ext.extract_and_save() == []
    ext.client.get_odds.assert_not_called()


def test_nenhum_fixture_cai_em_duas_janelas_na_mesma_passada(ext):
    # Bandas sobrepostas gastariam duas chamadas e gravariam duas linhas com rotulos
    # diferentes p/ o mesmo preco. Varre varios leads, um fixture por vez.
    ext.client.get_odds.return_value = _envelope_com_odds()

    for horas in (0.1, 0.2, 0.75, 1, 12, 22.5, 23.9, 30, 48, 72, 120, 167):
        ext.client.get_odds.reset_mock()
        ext.storage.upload_json.reset_mock()
        ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(horas)]

        ext.extract_and_save()

        assert ext.client.get_odds.call_count <= 1, f"lead de {horas}h caiu em 2 janelas"
        assert ext.storage.upload_json.call_count <= 1, f"lead de {horas}h gravou 2x"


# --------------------------------------------------------------------------- #
# Date-stamp e idempotencia diaria
# --------------------------------------------------------------------------- #
def test_a_captura_diaria_e_date_stampada(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    ext.extract_and_save()

    hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert ext.storage.upload_json.call_args.kwargs["date"] == hoje


def test_as_bandas_de_fechamento_nao_date_stampam(ext):
    # Sem date-stamp o nome do arquivo fica exatamente como hoje — o fato ja le assim.
    for horas, janela in ((23, "t24h"), (0.75, "t1h"), (0.1, "t15m")):
        ext.storage.upload_json.reset_mock()
        ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(horas)]
        ext.client.get_odds.return_value = _envelope_com_odds()

        ext.extract_and_save()

        kw = ext.storage.upload_json.call_args.kwargs
        assert kw["mode"] == janela
        assert kw.get("date") is None, f"{janela} nao pode date-stampar"


def test_captura_diaria_idempotente_dentro_do_dia(ext):
    # Segunda passada no mesmo dia: skip-if-exists por (fixture, janela, dia).
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.storage.bucket.blob.return_value.exists.return_value = True

    assert ext.extract_and_save() == []
    ext.client.get_odds.assert_not_called()
    ext.storage.upload_json.assert_not_called()


def test_o_skip_if_exists_da_diaria_olha_o_caminho_date_stampado(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = _envelope_com_odds()

    ext.extract_and_save()

    hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    consultado = ext.storage.bucket.blob.call_args.args[0]
    assert consultado == f"futebol/odds/raw_futebol_odds_999_daily_{hoje}.json"


# --------------------------------------------------------------------------- #
# Sem regressao
# --------------------------------------------------------------------------- #
def test_liga_fora_dos_targets_nao_gasta_chamada(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [
        _fixture(72, league_id=999999)
    ]

    assert ext.extract_and_save() == []
    ext.client.get_odds.assert_not_called()


def test_banda_de_fechamento_sem_odds_continua_sem_gravar(ext):
    # Banda curta (minutos) e forward-only: a casa pode publicar a qualquer momento, e
    # gravar aqui travaria o skip-if-exists e perderia a linha de fechamento.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(23)]
    ext.client.get_odds.return_value = {"errors": [], "response": []}

    assert ext.extract_and_save() == []
    ext.storage.upload_json.assert_not_called()


# --------------------------------------------------------------------------- #
# Vazio registrado na janela diaria
#
# Sem isso a banda diaria e uma bomba de cota: ela tem DIAS de largura, entao um fixture
# sem odds publicadas nunca grava arquivo, o skip-if-exists nunca trava, e o poll de 15min
# repergunta o mesmo vazio ~96x/dia por ate uma semana. Liga dormente (coverage.odds=FALSE
# ate a abertura) devolve vazio de proposito — e sao 5 delas armadas hoje.
# --------------------------------------------------------------------------- #
def test_janela_diaria_sem_odds_grava_o_vazio_registrado(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = {"errors": [], "response": []}

    ext.extract_and_save()

    ext.storage.upload_json.assert_called_once()
    kw = ext.storage.upload_json.call_args.kwargs
    assert kw["mode"] == "daily"
    assert kw["data"]["total_bookmakers"] == 0
    assert kw["data"]["fixture_id"] == 999
    assert kw["date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_vazio_registrado_da_diaria_nao_abre_o_gate_do_dbt(ext):
    # Arquivo sem casa nenhuma nao gera linha no fato (o UNNEST de bets vazio elimina a
    # linha no staging), entao nao ha rebuild a fazer.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.client.get_odds.return_value = {"errors": [], "response": []}

    assert ext.extract_and_save() == []
    ext.storage.upload_json.assert_called_once()


def test_com_o_vazio_gravado_a_diaria_nao_repergunta_no_mesmo_dia(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(72)]
    ext.storage.bucket.blob.return_value.exists.return_value = True

    ext.extract_and_save()

    ext.client.get_odds.assert_not_called()


def test_a_varredura_pede_ao_storage_o_horizonte_novo(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = []

    ext.extract_and_save()

    ext.storage.get_upcoming_fixtures_with_kickoff.assert_called_once_with(
        FUTEBOL_ODDS_HORIZON_MIN
    )
