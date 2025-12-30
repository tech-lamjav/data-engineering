"""Extractor para médias da temporada."""
from typing import Dict, Any, Optional, List
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class SeasonAveragesExtractor(BaseExtractor):
    """Extractor para médias da temporada."""
    
    def __init__(self, season: int = None):
        """Inicializa o extractor de médias."""
        super().__init__("season_averages", season)
    
    def extract(
        self,
        player_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Extrai médias da temporada.
        
        Args:
            player_ids: Lista de IDs de jogadores (opcional)
        
        Returns:
            Dicionário com os dados extraídos
        """
        logger.info(f"Extraindo médias da temporada {self.season}...")
        
        averages = self.client.get_season_averages(
            season=self.season,
            player_ids=player_ids,
            per_page=self.config.get("per_page", 100),
        )
        
        return {
            "season": self.season,
            "total_averages": len(averages),
            "averages": averages,
        }
