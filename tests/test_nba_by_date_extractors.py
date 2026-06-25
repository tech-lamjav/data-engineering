"""Achados #3 e #12/M14: extractors NBA por data compartilham o loop na BaseExtractor
(extract_and_save_by_date) e CONTABILIZAM falhas (failed) distinguindo-as de 'data sem
dados', emitindo um ERROR de RESUMO quando há falha.

Infra (GCSStorage/cliente) é mockada — nenhum acesso de rede.
"""
import logging
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def games_ext():
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.base_extractor.BallDontLieClient"):
        from src.extractors.games_extractor import GamesExtractor
        ext = GamesExtractor(season=2025)
        ext.storage = MagicMock()
        ext.client = MagicMock()
        ext.storage.upload_json.side_effect = lambda **kw: "gs://b/g.json"
        # Range curto e determinístico (2 datas).
        ext._get_season_date_range = lambda: ["2025-10-21", "2025-10-22"]
        yield ext


def test_falha_por_data_contabilizada_e_resumo_error(games_ext, caplog):
    # 1ª data falha (timeout), 2ª data retorna jogos.
    games_ext.client.get_games.side_effect = [
        RuntimeError("timeout 503"),
        [{"id": 1, "date": "2025-10-22"}],
    ]

    with caplog.at_level(logging.ERROR):
        paths = games_ext.extract_and_save()

    # Só a 2ª data salvou; a falha NÃO é mascarada como sucesso.
    assert len(paths) == 1
    # ERROR de RESUMO distinto de 'datas sem dados'.
    assert any("RESUMO DE FALHA" in r.message for r in caplog.records if r.levelno == logging.ERROR)


def test_sem_dados_nao_e_falha(games_ext, caplog):
    # Ambas as datas sem jogos (temporada vazia) — não conta como falha.
    games_ext.client.get_games.return_value = []

    with caplog.at_level(logging.ERROR):
        paths = games_ext.extract_and_save()

    assert paths == []
    assert not any("RESUMO DE FALHA" in r.message for r in caplog.records)


def test_sucesso_salva_todas_as_datas(games_ext):
    games_ext.client.get_games.return_value = [{"id": 1, "date": "2025-10-21"}]
    paths = games_ext.extract_and_save()
    assert len(paths) == 2
    assert games_ext.storage.upload_json.call_count == 2


def test_season_date_range_unificado_usa_fim_da_temporada():
    """M14: o range é unificado em NBA_SEASON_END_DATES (não mais o 30/jun hardcoded
    do antigo games_extractor)."""
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.base_extractor.BallDontLieClient"):
        from src.extractors.games_extractor import GamesExtractor
        from src.config import NBA_SEASON_END_DATES
        ext = GamesExtractor(season=2024)
        dates = ext._get_season_date_range()
        # 2024 tem fim conhecido em NBA_SEASON_END_DATES.
        assert dates[-1] == NBA_SEASON_END_DATES[2024]
        assert dates[0] == "2024-10-21"
