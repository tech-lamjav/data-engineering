"""Extractor para jogos."""
from typing import Dict, Any, Optional, List
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class GamesExtractor(BaseExtractor):
    """Extractor para dados de jogos."""
    
    def __init__(self, season: int = None):
        """Inicializa o extractor de jogos."""
        super().__init__("games", season)
    
    def extract(
        self,
        team_ids: Optional[List[int]] = None,
        dates: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Extrai dados de jogos.
        
        Args:
            team_ids: Lista de IDs de times (opcional)
            dates: Lista de datas no formato YYYY-MM-DD (opcional)
        
        Returns:
            Dicionário com os dados extraídos
        """
        logger.info(f"Extraindo jogos da temporada {self.season}...")
        
        games = self.client.get_games(
            season=self.season,
            team_ids=team_ids,
            dates=dates,
            per_page=self.config.get("per_page", 100),
        )
        
        return {
            "season": self.season,
            "total_games": len(games),
            "games": games,
        }
