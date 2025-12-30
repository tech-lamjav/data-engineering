"""Extractor para classificação de times."""
from typing import Dict, Any
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class TeamStandingsExtractor(BaseExtractor):
    """Extractor para classificação de times."""
    
    def __init__(self, season: int = None):
        """Inicializa o extractor de classificações."""
        super().__init__("team_standings", season)
    
    def extract(self) -> Dict[str, Any]:
        """
        Extrai classificação de times.
        
        Returns:
            Dicionário com os dados extraídos
        """
        logger.info(f"Extraindo classificação de times da temporada {self.season}...")
        
        standings = self.client.get_team_standings(
            season=self.season,
            per_page=self.config.get("per_page", 100),
        )
        
        return {
            "season": self.season,
            "total_teams": len(standings),
            "standings": standings,
        }
