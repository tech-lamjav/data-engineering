"""Achado #5: extractors latest-only de futebol NÃO podem sobrescrever o arquivo bom
no GCS com coleta PARCIAL quando um target obrigatório falha (errors no envelope).

Verifica que:
- target com `errors` => extract_and_save ABORTA (raise) e NÃO faz upload (preserva o
  snapshot bom anterior);
- 'liga sem dados legitimamente' (response vazio, sem errors) NÃO aborta — faz upload.

Infra (GCS/cliente API) é mockada — nenhum acesso de rede.
"""
from unittest.mock import MagicMock, patch

import pytest


def _make(extractor_cls_path, cls_name, client_get_attr):
    """Instancia o extractor com GCSStorage/ApiFootballClient mockados."""
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch(f"{extractor_cls_path}.ApiFootballClient"):
        module = __import__(extractor_cls_path, fromlist=[cls_name])
        ext = getattr(module, cls_name)(mode="current")
        ext.storage = MagicMock()
        ext.client = MagicMock()
        ext.storage.upload_json.return_value = "gs://b/file.json"
        # Garante exatamente 1 target (1 par league/season) p/ controle determinístico.
        ext.targets = [(71, 2026)]
        return ext, getattr(ext.client, client_get_attr)


CASES = [
    ("src.extractors.fixtures_extractor", "FixturesExtractor", "get_fixtures"),
    ("src.extractors.leagues_extractor", "LeaguesExtractor", "get_league"),
    ("src.extractors.players_extractor", "PlayersExtractor", "get_players"),
    ("src.extractors.teams_extractor", "TeamsExtractor", "get_teams"),
]


@pytest.mark.parametrize("mod,cls,getter", CASES)
def test_target_com_errors_nao_sobrescreve(mod, cls, getter):
    ext, get = _make(mod, cls, getter)
    get.return_value = {"errors": {"requests": "quota excedida"}, "response": []}

    with pytest.raises(RuntimeError):
        ext.extract_and_save()

    # O arquivo bom anterior é preservado: NENHUM upload é feito.
    ext.storage.upload_json.assert_not_called()


@pytest.mark.parametrize("mod,cls,getter", CASES)
def test_liga_sem_dados_legitimo_faz_upload(mod, cls, getter):
    ext, get = _make(mod, cls, getter)
    # response vazio SEM errors => liga sem dados legitimamente: salva (não aborta).
    get.return_value = {"errors": None, "response": []}

    path = ext.extract_and_save()

    assert path == "gs://b/file.json"
    ext.storage.upload_json.assert_called_once()
    # failed_targets não vaza pro payload.
    assert "failed_targets" not in ext.storage.upload_json.call_args.kwargs["data"]


@pytest.mark.parametrize("mod,cls,getter", CASES)
def test_sucesso_faz_upload(mod, cls, getter):
    ext, get = _make(mod, cls, getter)
    get.return_value = {"errors": None, "response": [{"x": 1}]}

    path = ext.extract_and_save()

    assert path == "gs://b/file.json"
    ext.storage.upload_json.assert_called_once()


def test_team_season_stats_aborta_com_falha():
    """team_season_stats: se algum time falhar (errors/exceção), não sobrescreve."""
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.team_season_stats_extractor.ApiFootballClient"), \
         patch("src.extractors.team_season_stats_extractor.time.sleep", lambda *_: None):
        from src.extractors.team_season_stats_extractor import TeamSeasonStatsExtractor
        ext = TeamSeasonStatsExtractor(mode="current")
        ext.storage = MagicMock()
        ext.client = MagicMock()
        ext.storage.get_team_ids_from_storage.return_value = [
            {"team_id": 1, "league_id": 71, "season": 2026},
        ]
        ext.client.get_team_season_stats.return_value = {
            "errors": {"requests": "quota"}, "response": {}
        }

        with pytest.raises(RuntimeError):
            ext.extract_and_save()
        ext.storage.upload_json.assert_not_called()


def test_team_season_stats_sucesso_faz_upload():
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.team_season_stats_extractor.ApiFootballClient"), \
         patch("src.extractors.team_season_stats_extractor.time.sleep", lambda *_: None):
        from src.extractors.team_season_stats_extractor import TeamSeasonStatsExtractor
        ext = TeamSeasonStatsExtractor(mode="current")
        ext.storage = MagicMock()
        ext.client = MagicMock()
        ext.storage.upload_json.return_value = "gs://b/tss.json"
        ext.storage.get_team_ids_from_storage.return_value = [
            {"team_id": 1, "league_id": 71, "season": 2026},
        ]
        ext.client.get_team_season_stats.return_value = {
            "errors": None,
            "response": {"team": {"id": 1}, "goals": {}, "fixtures": {}},
        }

        path = ext.extract_and_save()
        assert path == "gs://b/tss.json"
        ext.storage.upload_json.assert_called_once()
        assert "failed" not in ext.storage.upload_json.call_args.kwargs["data"]
