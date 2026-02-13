"""Extractor para médias da temporada por time."""
from typing import Dict, Any, Optional, List
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class TeamSeasonAveragesExtractor(BaseExtractor):
    """Extractor para médias da temporada por time."""

    def __init__(
        self,
        season: int = None,
        category: str = "general",
        type: str = "advanced",
        season_type: str = "regular",
    ):
        """
        Inicializa o extractor de médias por time.

        Args:
            season: Ano da temporada
            category: Categoria (general, clutch, shooting, playtype, tracking, hustle, shotdashboard)
            type: Tipo de estatística (base, advanced, scoring, misc, etc.)
            season_type: Tipo de temporada (regular, playoffs, ist)
        """
        super().__init__("team_season_averages", season)
        self.category = category
        self.type = type
        self.season_type = season_type

    def extract(
        self,
        team_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Extrai médias da temporada por time.

        Args:
            team_ids: Lista de IDs de times (opcional)

        Returns:
            Dicionário com os dados extraídos
        """
        logger.info(
            f"Extraindo médias da temporada por time {self.season} "
            f"(category={self.category}, type={self.type}, season_type={self.season_type})..."
        )

        team_averages = self.client.get_team_season_averages(
            season=self.season,
            category=self.category,
            type=self.type,
            season_type=self.season_type,
            team_ids=team_ids,
            per_page=self.config.get("per_page", 100),
        )

        return {
            "season": self.season,
            "category": self.category,
            "type": self.type,
            "season_type": self.season_type,
            "total_team_averages": len(team_averages),
            "team_averages": team_averages,
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
            date: Data no formato YYYY-MM-DD (opcional, não usado para team_season_averages)

        Returns:
            Caminho completo do arquivo no GCS
        """
        return self.storage.upload_json(
            data=data,
            endpoint=self.endpoint_name,
            season=self.season,
            category=self.category,
            type=self.type,
        )
