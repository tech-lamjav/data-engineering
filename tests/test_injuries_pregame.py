"""Testes do poll pré-jogo de desfalques (InjuriesExtractor mode="pregame").

Cobre a decisão de BANDA: quais fixtures NS entram na janela e gastam chamada, e quais
ficam de fora sem custo. A banda foi estreitada de 14 dias para ~96h porque a fonte só
publica a lista a 53–70h do apito — os primeiros 11 dias e meio da banda antiga nunca
devolviam nada e o poll horário repergunta o mesmo vazio até o kickoff.

Infra (GCS/API) é mockada — nenhum acesso de rede, no idioma de
tests/test_per_fixture_extractor.py.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.config import (
    BRASILEIRAO_ID,
    FUTEBOL_INJURIES_HORIZON_MIN,
    FUTEBOL_INJURIES_WINDOWS,
)


@pytest.fixture
def ext():
    # Patches só p/ o __init__ não instanciar GCSStorage()/ApiFootballClient() reais.
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.injuries_extractor.ApiFootballClient"), \
         patch("src.extractors.injuries_extractor.time.sleep", lambda *_: None):
        from src.extractors.injuries_extractor import InjuriesExtractor

        e = InjuriesExtractor(mode="pregame")
        e.storage = MagicMock()
        e.client = MagicMock()
        e.storage.bucket.blob.return_value.exists.return_value = False
        e.storage.upload_json.side_effect = (
            lambda **kw: f"gs://b/injuries/{kw['game_id']}_{kw['mode']}_{kw['date']}.json"
        )
        yield e


def _fixture(horas_ate_o_apito, fixture_id=999, league_id=BRASILEIRAO_ID):
    """Fixture NS com kickoff a N horas de agora (o extractor deriva o lead de now())."""
    kickoff = datetime.now(timezone.utc) + timedelta(hours=horas_ate_o_apito)
    return {
        "fixture_id": fixture_id,
        "league_id": league_id,
        "season": 2026,
        "kickoff_ts": int(kickoff.timestamp()),
    }


def _envelope_com_desfalque():
    return {
        "errors": [],
        "response": [
            {
                "player": {"id": 1, "name": "X", "type": "Missing Fixture", "reason": "Injury"},
                "team": {"id": 10},
                "fixture": {"id": 999},
                "league": {"id": BRASILEIRAO_ID},
            }
        ],
    }


# --------------------------------------------------------------------------- #
# A banda é um número declarado
# --------------------------------------------------------------------------- #
def test_a_banda_deriva_do_horizonte_declarado():
    # AC: "mudar o horizonte é mudar um número, não acrescentar banda nova". Sem pinar o
    # valor: a config manda rechecar a banda com amostra europeia, e um teste que quebra
    # nessa retuning seria um detector de mudanca, nao de regressao.
    assert FUTEBOL_INJURIES_WINDOWS == {"daily": (0, FUTEBOL_INJURIES_HORIZON_MIN)}


def test_a_banda_cobre_a_faixa_em_que_a_fonte_publica():
    # A fonte publica a 53–70h do apito (mediana 58h, 28 fixtures do Brasileirao).
    _, teto = FUTEBOL_INJURIES_WINDOWS["daily"]
    assert teto >= 70 * 60  # folga sobre o limite superior observado
    assert teto < 14 * 24 * 60  # e nao a varredura de 14 dias de antes


# --------------------------------------------------------------------------- #
# Dentro / fora da banda
# --------------------------------------------------------------------------- #
def test_fixture_fora_da_banda_nao_gasta_chamada(ext):
    # 200h do apito: a fonte nao teria lista, e antes o poll perguntava de hora em hora.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(200)]

    paths = ext.extract_and_save()

    assert paths == []
    ext.client.get_injuries_by_fixture.assert_not_called()
    ext.storage.upload_json.assert_not_called()


def test_fixture_logo_acima_do_teto_fica_de_fora(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(97)]

    paths = ext.extract_and_save()

    assert paths == []
    ext.client.get_injuries_by_fixture.assert_not_called()


def test_fixture_dentro_da_banda_e_coletado(ext):
    # 60h: mediana observada de publicacao da fonte.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(60)]
    ext.client.get_injuries_by_fixture.return_value = _envelope_com_desfalque()

    paths = ext.extract_and_save()

    assert len(paths) == 1
    ext.client.get_injuries_by_fixture.assert_called_once_with(999)
    kw = ext.storage.upload_json.call_args.kwargs
    assert kw["endpoint"] == "injuries"
    assert kw["sport"] == "futebol"
    assert kw["game_id"] == 999
    assert kw["mode"] == "daily"


def test_fixture_logo_abaixo_do_teto_ainda_entra(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(95)]
    ext.client.get_injuries_by_fixture.return_value = _envelope_com_desfalque()

    assert len(ext.extract_and_save()) == 1


def test_fixture_perto_do_apito_continua_entrando(ext):
    # A banda comeca em 0: fixture de hoje a noite nao pode cair fora por causa do estreitamento.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(2)]
    ext.client.get_injuries_by_fixture.return_value = _envelope_com_desfalque()

    assert len(ext.extract_and_save()) == 1


def test_a_varredura_pede_ao_storage_so_o_horizonte_novo(ext):
    # A economia real: o poll nem carrega os fixtures alem da banda.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = []

    ext.extract_and_save()

    ext.storage.get_upcoming_fixtures_with_kickoff.assert_called_once_with(
        FUTEBOL_INJURIES_HORIZON_MIN
    )


# --------------------------------------------------------------------------- #
# Sem regressao no que ja funcionava
# --------------------------------------------------------------------------- #
def test_liga_sem_coverage_continua_fora(ext):
    # Copa do Mundo (1) nao tem coverage.injuries — nao gasta chamada, como antes.
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [
        _fixture(60, league_id=1)
    ]

    assert ext.extract_and_save() == []
    ext.client.get_injuries_by_fixture.assert_not_called()


def test_skip_if_exists_continua_travando_no_mesmo_dia(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = [_fixture(60)]
    ext.storage.bucket.blob.return_value.exists.return_value = True

    assert ext.extract_and_save() == []
    ext.client.get_injuries_by_fixture.assert_not_called()


def test_sem_fixtures_no_horizonte_nao_quebra(ext):
    ext.storage.get_upcoming_fixtures_with_kickoff.return_value = []

    assert ext.extract_and_save() == []
    ext.client.get_injuries_by_fixture.assert_not_called()
