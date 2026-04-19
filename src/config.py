"""Configurações centralizadas do projeto."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis de ambiente do arquivo .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# API Configuration
API_BASE_URL = "https://api.balldontlie.io/v1"
API_BASE_URL_V2 = "https://api.balldontlie.io/v2"  # Para endpoints v2
API_BASE_URL_NBA = "https://api.balldontlie.io/nba/v1"  # Para team_season_averages
API_KEY = os.getenv("BALLDONTLIE_KEY")
API_TIMEOUT = 60

# GCS Configuration
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "smartbetting-landing")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCS_USE_ADC = True  # Application Default Credentials

# BigQuery Configuration
BIGQUERY_PROJECT_ID = "smartbetting-dados"
BIGQUERY_DATASET = "nba"
BIGQUERY_LOCATION = "us-east1"

# Season Configuration
SEASON = int(os.getenv("SEASON", "2025"))

# Temporadas a extrair (backfill + corrente)
SEASONS = [2023, 2024, 2025]

# Data de fim para temporadas já encerradas (corrente usa datetime.now())
NBA_SEASON_END_DATES = {
    2023: "2024-06-17",  # Finals 2023-24: Boston x Dallas
    2024: "2025-06-22",  # Finals 2024-25 (estimado)
}

# Janelas play-in e playoffs por temporada
NBA_SEASON_TYPE_DATES = {
    2023: {"playin_start": "2024-04-16", "playoffs_start": "2024-04-20"},
    2024: {"playin_start": "2025-04-15", "playoffs_start": "2025-04-19"},
    2025: {"playin_start": "2026-04-14", "playoffs_start": "2026-04-18"},
}


def get_season_type_for_date(game_date: str, season: int) -> str:
    """Retorna 'regular', 'playin' ou 'playoffs' com base na data do jogo."""
    dates = NBA_SEASON_TYPE_DATES.get(season, {})
    if not dates:
        return "regular"
    if game_date >= dates.get("playoffs_start", "9999"):
        return "playoffs"
    if game_date >= dates.get("playin_start", "9999"):
        return "playin"
    return "regular"

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Endpoint-specific configurations
ENDPOINT_CONFIGS = {
    "games": {
        "has_date": True,
        "per_page": 100,
    },
    "game_player_stats": {
        "has_date": True,
        "per_page": 100,
    },
    "season_averages": {
        "has_date": False,
        "per_page": 100,
    },
    "team_season_averages": {
        "has_date": False,
        "per_page": 100,
    },
    "active_players": {
        "has_date": False,
        "per_page": 100,
    },
    "player_injuries": {
        "has_date": False,
        "per_page": 100,
    },
    "team_standings": {
        "has_date": False,
        "per_page": 100,
    },
    "player_props": {
        "has_date": True,
        "per_page": 100,
        "market": "draftkings",
        "vendors": [
            "draftkings",
            "caesars",
            "betrivers",
        ],
    },
    "betting_odds": {
        "has_date": False,
        "per_page": 100,
    },
    "game_player_stats_period": {
        "has_date": True,
        "per_page": 100,
        "periods": [1, 2, 3, 4],
    },
}

# GCS Path Structure
def get_gcs_path(
    endpoint: str,
    season: int,
    date: str = None,
    market: str = None,
    game_id: int = None,
    category: str = None,
    type: str = None,
    season_type: str = None,
    period: int = None,
) -> str:
    """
    Gera o caminho no GCS seguindo a estrutura definida.

    Args:
        endpoint: Nome do endpoint (ex: 'games', 'active_players')
        season: Ano da temporada (ex: 2025)
        date: Data no formato YYYY-MM-DD (opcional)
        market: Market para player_props (opcional)
        game_id: Game ID para player_props (opcional)
        category: Categoria para season_averages (opcional)
        type: Tipo para season_averages (opcional)
        season_type: Tipo de temporada para season_averages (ex: regular, playoffs, ist)

    Returns:
        Caminho completo no formato: nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}.json
        ou nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{date}.json
        ou nba/{endpoint}/{season}/{market}/raw_nba_{endpoint}_{season}-{game_id}.json (para player_props)
        ou nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{category}-{type}-{season_type}.json (para season_averages)
    """
    if market and game_id:
        # Estrutura especial para player_props: nba/player_props/{season}/{market}/raw_nba_player_props_{season}-{game_id}.json
        filename = f"raw_nba_{endpoint}_{season}-{game_id}.json"
        return f"nba/{endpoint}/{season}/{market}/{filename}"
    elif game_id and not market:
        # Estrutura para endpoints com game_id sem vendor: nba/betting_odds/{season}/raw_nba_betting_odds_{season}-{game_id}.json
        filename = f"raw_nba_{endpoint}_{season}-{game_id}.json"
        return f"nba/{endpoint}/{season}/{filename}"
    elif category and type:
        # Estrutura para season_averages: nba/season_averages/{season}/raw_nba_season_averages_{season}-{category}-{type}-{season_type}.json
        suffix = f"-{season_type}" if season_type else ""
        filename = f"raw_nba_{endpoint}_{season}-{category}-{type}{suffix}.json"
        return f"nba/{endpoint}/{season}/{filename}"
    elif period and date:
        filename = f"raw_nba_{endpoint}_{season}-{date}.json"
        return f"nba/{endpoint}/{season}/q{period}/{filename}"
    elif date:
        filename = f"raw_nba_{endpoint}_{season}-{date}.json"
        return f"nba/{endpoint}/{season}/{filename}"
    else:
        filename = f"raw_nba_{endpoint}_{season}.json"
        return f"nba/{endpoint}/{season}/{filename}"

