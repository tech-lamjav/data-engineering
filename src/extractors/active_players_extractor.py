"""Extractor para jogadores ativos."""
from typing import Dict, Any
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class ActivePlayersExtractor(BaseExtractor):
    """Extractor para jogadores ativos."""
    
    def __init__(self, season: int = None):
        """Inicializa o extractor de jogadores ativos."""
        super().__init__("active_players", season)
    
    def extract(self) -> Dict[str, Any]:
        """
        Extrai lista de jogadores ativos.
        
        Returns:
            Dicionário com os dados extraídos
        """
        logger.info("Extraindo lista de jogadores ativos...")
        
        players = self.client.get_active_players(
            per_page=self.config.get("per_page", 100),
        )
        
        return {
            "season": self.season,
            "total_players": len(players),
            "players": players,
        }
