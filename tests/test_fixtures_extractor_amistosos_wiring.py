"""Testes da fiação do universo de amistosos dentro de FixturesExtractor.extract() (DE#94,
ADR 0004): lê/grava o estado append-only em GCS, bootstrapa a partir do catálogo de teams e
aplica o filtro (funções puras do DE#93) só nas linhas de AMISTOSOS_ID, sem tocar as demais
ligas. GCS e API mockados — nenhum acesso de rede.
"""
import logging
from unittest.mock import MagicMock, patch

import pytest

from src.config import AMISTOSOS_ID


@pytest.fixture
def ext():
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.fixtures_extractor.ApiFootballClient"):
        from src.extractors.fixtures_extractor import FixturesExtractor
        e = FixturesExtractor(mode="current")
        e.storage = MagicMock()
        e.client = MagicMock()
        yield e


def _envelope(items):
    return {"errors": None, "response": items}


def _fixture_item(fixture_id, home_id, away_id, status="FT"):
    return {
        "fixture": {"id": fixture_id, "status": {"short": status}},
        "teams": {"home": {"id": home_id}, "away": {"id": away_id}},
        "goals": {"home": 1, "away": 0},
    }


def _team(team_id):
    return {"team_id": team_id, "league_id": 71, "season": 2026}


def test_liga_sem_amistosos_nao_toca_o_universo(ext):
    """Sem AMISTOSOS_ID nos targets, nenhum estado é lido/gravado — não gasta round-trip
    de GCS à toa em toda extração diária das outras 13 ligas."""
    ext.targets = [(71, 2026)]
    ext.client.get_fixtures.return_value = _envelope([_fixture_item(1, 10, 20)])

    data = ext.extract()

    assert data["total_fixtures"] == 1
    ext.storage.get_known_team_ids_from_storage.assert_not_called()
    ext.storage.get_team_ids_from_storage.assert_not_called()
    ext.storage.upload_known_team_ids.assert_not_called()


def test_amistoso_com_os_dois_times_conhecidos_entra(ext):
    ext.targets = [(71, 2026), (AMISTOSOS_ID, 2026)]
    ext.client.get_fixtures.side_effect = [
        _envelope([_fixture_item(1, 10, 20)]),  # liga 71 (não-amistoso)
        _envelope([_fixture_item(2, 10, 20)]),  # amistoso, times já conhecidos
    ]
    ext.storage.get_known_team_ids_from_storage.return_value = set()  # bootstrap
    ext.storage.get_team_ids_from_storage.side_effect = lambda mode: (
        [_team(10), _team(20)] if mode == "current" else []
    )

    data = ext.extract()

    fixture_ids = sorted(row["fixture"]["id"] for row in data["fixtures"])
    assert fixture_ids == [1, 2]
    ext.storage.upload_known_team_ids.assert_called_once_with({10, 20})


def test_amistoso_com_um_time_desconhecido_fica_de_fora(ext):
    ext.targets = [(AMISTOSOS_ID, 2026)]
    ext.client.get_fixtures.return_value = _envelope([_fixture_item(2, 10, 999)])
    ext.storage.get_known_team_ids_from_storage.return_value = set()
    ext.storage.get_team_ids_from_storage.side_effect = lambda mode: (
        [_team(10)] if mode == "current" else []
    )

    data = ext.extract()

    assert data["fixtures"] == []
    assert data["total_fixtures"] == 0


def test_outras_ligas_nao_sao_filtradas_pelo_universo(ext):
    """O recorte só se aplica às linhas de AMISTOSOS_ID — um jogo de outra liga com times
    fora do catálogo (ex.: temporada nova ainda sem /teams coletado) não é descartado."""
    ext.targets = [(71, 2026), (AMISTOSOS_ID, 2026)]
    ext.client.get_fixtures.side_effect = [
        _envelope([_fixture_item(1, 555, 666)]),  # liga 71: times fora do catálogo — mantém
        _envelope([_fixture_item(2, 555, 666)]),  # amistoso: mesmos times, mas fora — descarta
    ]
    ext.storage.get_known_team_ids_from_storage.return_value = set()
    ext.storage.get_team_ids_from_storage.return_value = []  # catálogo vazio nas duas modes

    data = ext.extract()

    fixture_ids = [row["fixture"]["id"] for row in data["fixtures"]]
    assert fixture_ids == [1]


def test_estado_anterior_e_unido_append_only(ext):
    """Um time que saiu do catálogo desta execução (ex.: liga removida da config) continua
    valendo para o universo — a união é append-only (ADR 0004, decisão 10)."""
    ext.targets = [(AMISTOSOS_ID, 2026)]
    ext.client.get_fixtures.return_value = _envelope([_fixture_item(3, 30, 40)])
    ext.storage.get_known_team_ids_from_storage.return_value = {30, 40, 999}
    ext.storage.get_team_ids_from_storage.return_value = []  # catálogo desta execução vazio

    data = ext.extract()

    fixture_ids = [row["fixture"]["id"] for row in data["fixtures"]]
    assert fixture_ids == [3]  # 30 e 40 sobrevivem via estado anterior, não via catálogo novo
    ext.storage.upload_known_team_ids.assert_called_once_with({30, 40, 999})


def test_crescimento_anomalo_loga_error_mas_nao_aborta(ext, caplog):
    ext.targets = [(AMISTOSOS_ID, 2026)]
    ext.client.get_fixtures.return_value = _envelope([_fixture_item(4, 1, 2)])
    ext.storage.get_known_team_ids_from_storage.return_value = {1, 2}
    novos = range(100, 130)  # 30 times novos, acima do max_growth default (20)
    ext.storage.get_team_ids_from_storage.side_effect = lambda mode: (
        [_team(tid) for tid in novos] if mode == "current" else []
    )

    with caplog.at_level(logging.ERROR):
        data = ext.extract()

    assert any("ANÔMALA" in r.message for r in caplog.records if r.levelno == logging.ERROR)
    assert data["total_fixtures"] == 1  # guarda só sinaliza — o jogo (times 1/2, conhecidos) entra
