"""Extractor para lesões de jogadores."""
from typing import Dict, Any
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class PlayerInjuriesExtractor(BaseExtractor):
    """Extractor para lesões de jogadores."""
    
    def __init__(self, season: int = None):
        """Inicializa o extractor de lesões."""
        super().__init__("player_injuries", season)
    
    def extract(self) -> Dict[str, Any]:
        """
        Extrai lista de lesões de jogadores.
        
        Returns:
            Dicionário com os dados extraídos
        """
        logger.info("Extraindo lista de lesões de jogadores...")
        
        injuries = self.client.get_player_injuries(
            per_page=self.config.get("per_page", 100),
        )
        
        return {
            "season": self.season,
            "total_injuries": len(injuries),
            "injuries": injuries,
        }
