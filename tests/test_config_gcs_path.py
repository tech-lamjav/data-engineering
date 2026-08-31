"""Testes de get_gcs_path — NBA, futebol e regressão do fix da janela t15m de odds."""
import pytest

from src.config import get_gcs_path, FUTEBOL_ODDS_WINDOWS


# --------------------------------------------------------------------------- #
# Futebol
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("window", list(FUTEBOL_ODDS_WINDOWS))
def test_futebol_odds_toda_janela_tem_sufixo(window):
    # Regressão do bug t15m: TODA janela de FUTEBOL_ODDS_WINDOWS deve gerar sufixo.
    p = get_gcs_path("odds", 2025, game_id=123, sport="futebol", mode=window)
    assert p == f"futebol/odds/raw_futebol_odds_123_{window}.json"


def test_futebol_t15m_explicito():
    assert (
        get_gcs_path("odds", 2025, game_id=999, sport="futebol", mode="t15m")
        == "futebol/odds/raw_futebol_odds_999_t15m.json"
    )


@pytest.mark.parametrize("phase", ["confirmed", "real"])
def test_futebol_lineups_fases(phase):
    assert (
        get_gcs_path("fixture_lineups", 2025, game_id=55, sport="futebol", mode=phase)
        == f"futebol/fixture_lineups/raw_futebol_fixture_lineups_55_{phase}.json"
    )


def test_futebol_per_fixture_sem_mode():
    assert (
        get_gcs_path("fixture_statistics", 2025, game_id=7, sport="futebol")
        == "futebol/fixture_statistics/raw_futebol_fixture_statistics_7.json"
    )


def test_futebol_mode_desconhecido_nao_vaza_no_nome():
    # mode fora do whitelist (e que não é janela) não deve aparecer no arquivo.
    assert (
        get_gcs_path("fixture_events", 2025, game_id=7, sport="futebol", mode="qualquer")
        == "futebol/fixture_events/raw_futebol_fixture_events_7.json"
    )


@pytest.mark.parametrize("mode", ["current", "backfill", "live"])
def test_futebol_latest_only_mode(mode):
    assert (
        get_gcs_path("fixtures", 2025, sport="futebol", mode=mode)
        == f"futebol/fixtures/raw_futebol_fixtures_{mode}.json"
    )


def test_futebol_snapshot_com_data():
    assert (
        get_gcs_path("standings", 2025, date="2025-10-21", sport="futebol", mode="current")
        == "futebol/standings/raw_futebol_standings_current_2025-10-21.json"
    )


def test_futebol_sem_mode_sem_data():
    assert (
        get_gcs_path("leagues", 2025, sport="futebol")
        == "futebol/leagues/raw_futebol_leagues.json"
    )


# --------------------------------------------------------------------------- #
# NBA
# --------------------------------------------------------------------------- #
def test_nba_base():
    assert get_gcs_path("games", 2025) == "nba/games/2025/raw_nba_games_2025.json"


def test_nba_com_data():
    assert (
        get_gcs_path("games", 2025, date="2025-10-21")
        == "nba/games/2025/raw_nba_games_2025-2025-10-21.json"
    )


def test_nba_player_props_market_e_gameid():
    assert (
        get_gcs_path("player_props", 2025, market="draftkings", game_id=42)
        == "nba/player_props/2025/draftkings/raw_nba_player_props_2025-42.json"
    )


def test_nba_betting_odds_gameid_sem_market():
    assert (
        get_gcs_path("betting_odds", 2025, game_id=42)
        == "nba/betting_odds/2025/raw_nba_betting_odds_2025-42.json"
    )


def test_nba_season_averages_categoria_tipo_seasontype():
    assert (
        get_gcs_path("season_averages", 2025, category="general", type="base", season_type="regular")
        == "nba/season_averages/2025/raw_nba_season_averages_2025-general-base-regular.json"
    )


def test_nba_season_averages_sem_season_type():
    assert (
        get_gcs_path("season_averages", 2025, category="general", type="base")
        == "nba/season_averages/2025/raw_nba_season_averages_2025-general-base.json"
    )


def test_nba_period_vai_para_subdir_q():
    assert (
        get_gcs_path("game_player_stats_period", 2025, date="2025-10-21", period=1)
        == "nba/game_player_stats_period/2025/q1/raw_nba_game_player_stats_period_2025-2025-10-21.json"
    )
