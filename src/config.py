"""Configurações centralizadas do projeto."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis de ambiente do arquivo .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# API Configuration
API_BASE_URL = "https://api.balldontlie.io/v1"
API_KEY = os.getenv("BALLDONTLIE_KEY")
API_TIMEOUT = 60

# GCS Configuration
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "smartbetting-landing")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCS_USE_ADC = True  # Application Default Credentials

# Season Configuration
SEASON = int(os.getenv("SEASON", "2025"))

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
    },
}

# GCS Path Structure
def get_gcs_path(endpoint: str, season: int, date: str = None) -> str:
    """
    Gera o caminho no GCS seguindo a estrutura definida.
    
    Args:
        endpoint: Nome do endpoint (ex: 'games', 'active_players')
        season: Ano da temporada (ex: 2025)
        date: Data no formato YYYY-MM-DD (opcional)
    
    Returns:
        Caminho completo no formato: nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}.json
        ou nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{date}.json
    """
    if date:
        filename = f"raw_nba_{endpoint}_{season}-{date}.json"
    else:
        filename = f"raw_nba_{endpoint}_{season}.json"
    
    return f"nba/{endpoint}/{season}/{filename}"

