"""Achados #11/M15 e #3: betting_odds e player_props (per-game NBA) aplicam
skip-if-exists + janela de re-fetch (como o futebol) e contabilizam falhas.

- Jogo ANTIGO (fora da janela) com arquivo já existente => PULA (não chama a API).
- Jogo RECENTE (dentro da janela) => re-busca mesmo se o arquivo existir.
- Falha por jogo é contada (não mascarada) e gera ERROR de RESUMO.

Infra (GCSStorage/cliente) é mockada — nenhum acesso de rede.
"""
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _hoje():
    return datetime.now(timezone.utc).date()


@pytest.fixture
def odds_ext():
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.base_extractor.BallDontLieClient"), \
         patch("src.extractors.betting_odds_extractor.time.sleep", lambda *_: None):
        from src.extractors.betting_odds_extractor import BettingOddsExtractor
        ext = BettingOddsExtractor(season=2025)
        ext.storage = MagicMock()
        ext.client = MagicMock()
        ext.storage.upload_json.side_effect = lambda **kw: f"gs://b/{kw['game_id']}.json"
        yield ext


def test_skip_if_exists_jogo_antigo(odds_ext):
    antigo = (_hoje() - timedelta(days=30)).strftime("%Y-%m-%d")
    odds_ext.storage.get_game_ids_from_storage.return_value = [10]
    odds_ext.storage.get_game_dates_from_storage.return_value = {10: antigo}
    odds_ext.storage.bucket.blob.return_value.exists.return_value = True  # arquivo já existe

    paths = odds_ext.extract_and_save()

    assert paths == []
    odds_ext.client.get_betting_odds.assert_not_called()  # pulou sem gastar chamada
    odds_ext.storage.upload_json.assert_not_called()


def test_refetch_jogo_recente(odds_ext):
    recente = _hoje().strftime("%Y-%m-%d")  # dentro da janela
    odds_ext.storage.get_game_ids_from_storage.return_value = [11]
    odds_ext.storage.get_game_dates_from_storage.return_value = {11: recente}
    odds_ext.storage.bucket.blob.return_value.exists.return_value = True  # existe, mas re-busca
    odds_ext.client.get_betting_odds.return_value = [{"vendor": "dk", "spread": -3}]

    paths = odds_ext.extract_and_save()

    assert len(paths) == 1
    odds_ext.client.get_betting_odds.assert_called_once()


def test_jogo_sem_data_conhecida_refetch(odds_ext):
    odds_ext.storage.get_game_ids_from_storage.return_value = [12]
    odds_ext.storage.get_game_dates_from_storage.return_value = {}  # sem data => re-busca
    odds_ext.storage.bucket.blob.return_value.exists.return_value = True
    odds_ext.client.get_betting_odds.return_value = [{"vendor": "dk"}]

    paths = odds_ext.extract_and_save()
    assert len(paths) == 1
    odds_ext.client.get_betting_odds.assert_called_once()


def test_falha_por_jogo_contabilizada(odds_ext, caplog):
    recente = _hoje().strftime("%Y-%m-%d")
    odds_ext.storage.get_game_ids_from_storage.return_value = [20, 21]
    odds_ext.storage.get_game_dates_from_storage.return_value = {20: recente, 21: recente}
    odds_ext.storage.bucket.blob.return_value.exists.return_value = False
    odds_ext.client.get_betting_odds.side_effect = [RuntimeError("503"), [{"vendor": "dk"}]]

    with caplog.at_level(logging.ERROR):
        paths = odds_ext.extract_and_save()

    assert len(paths) == 1  # só o 2º
    assert any("RESUMO DE FALHA" in r.message for r in caplog.records if r.levelno == logging.ERROR)


@pytest.fixture
def props_ext():
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.base_extractor.BallDontLieClient"), \
         patch("src.extractors.player_props_extractor.time.sleep", lambda *_: None):
        from src.extractors.player_props_extractor import PlayerPropsExtractor
        ext = PlayerPropsExtractor(season=2025)
        ext.storage = MagicMock()
        ext.client = MagicMock()
        ext.vendors = ["draftkings"]
        ext.storage.upload_json.side_effect = lambda **kw: f"gs://b/{kw['game_id']}.json"
        yield ext


def test_props_skip_if_exists_jogo_antigo(props_ext):
    antigo = (_hoje() - timedelta(days=30)).strftime("%Y-%m-%d")
    props_ext.storage.get_game_ids_from_storage.return_value = [10]
    props_ext.storage.get_game_dates_from_storage.return_value = {10: antigo}
    props_ext.storage.bucket.blob.return_value.exists.return_value = True

    paths = props_ext.extract_and_save()

    assert paths == []
    props_ext.client.get_player_props.assert_not_called()


def test_props_refetch_jogo_recente(props_ext):
    recente = _hoje().strftime("%Y-%m-%d")
    props_ext.storage.get_game_ids_from_storage.return_value = [11]
    props_ext.storage.get_game_dates_from_storage.return_value = {11: recente}
    props_ext.storage.bucket.blob.return_value.exists.return_value = True
    props_ext.client.get_player_props.return_value = [{"player": "x", "line": 25.5}]

    paths = props_ext.extract_and_save()

    assert len(paths) == 1
    props_ext.client.get_player_props.assert_called_once()
