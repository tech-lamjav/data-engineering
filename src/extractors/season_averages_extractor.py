"""Extractor para médias da temporada."""
from typing import Dict, Any, Optional, List
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class SeasonAveragesExtractor(BaseExtractor):
    """Extractor para médias da temporada."""
    
    def __init__(self, season: int = None, category: str = "general", type: str = "base", season_type: str = "regular"):
        """
        Inicializa o extractor de médias.
        
        Args:
            season: Ano da temporada
            category: Categoria (general, clutch, defense, shooting)
            type: Tipo de estatística (base, advanced, usage, scoring, defense, misc, etc.)
            season_type: Tipo de temporada (regular, playoffs, ist, playin)
        """
        super().__init__("season_averages", season)
        self.category = category
        self.type = type
        self.season_type = season_type
    
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
        logger.info(
            f"Extraindo médias da temporada {self.season} "
            f"(category={self.category}, type={self.type}, season_type={self.season_type})..."
        )
        
        averages = self.client.get_season_averages(
            season=self.season,
            category=self.category,
            type=self.type,
            season_type=self.season_type,
            player_ids=player_ids,
            per_page=self.config.get("per_page", 100),
        )
        
        return {
            "season": self.season,
            "category": self.category,
            "type": self.type,
            "season_type": self.season_type,
            "total_averages": len(averages),
            "averages": averages,
        }
    
    def save_to_gcs(
        self,
        data: Dict[str, Any],
        date: Optional[str] = None,
    ) -> str:
        """
        Salva dados no GCS.
        
        Args:
            data: Dados a serem salvos
            date: Data no formato YYYY-MM-DD (opcional, não usado para season_averages)
        
        Returns:
            Caminho completo do arquivo no GCS
        """
        return self.storage.upload_json(
            data=data,
            endpoint=self.endpoint_name,
            season=self.season,
            category=self.category,
            type=self.type,
            season_type=self.season_type,
        )
