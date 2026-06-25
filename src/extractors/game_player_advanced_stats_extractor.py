"""Extractor para estatísticas avançadas (tracking/passing) de jogadores por jogo."""
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from src.extractors.base_extractor import BaseExtractor
from src.config import get_season_type_for_date
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class GamePlayerAdvancedStatsExtractor(BaseExtractor):
    """Extractor para estatísticas avançadas de jogadores por jogo (endpoint /nba/v2/stats/advanced)."""

    def __init__(self, season: int = None):
        """Inicializa o extractor de estatísticas avançadas."""
        super().__init__("game_player_advanced_stats", season)

    def extract(
        self,
        game_ids: Optional[List[int]] = None,
        player_ids: Optional[List[int]] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extrai estatísticas avançadas de jogadores por jogo.

        Args:
            game_ids: Lista de IDs de jogos (opcional)
            player_ids: Lista de IDs de jogadores (opcional)
            date: Data no formato YYYY-MM-DD (opcional)

        Returns:
            Dicionário com os dados extraídos
        """
        dates = [date] if date else None

        logger.info(f"Extraindo estatísticas avançadas para temporada {self.season}...")
        if date:
            logger.info(f"Filtro por data: {date}")
        if game_ids:
            logger.info(f"Filtro por game_ids: {game_ids}")
        if player_ids:
            logger.info(f"Filtro por player_ids: {player_ids}")

        stats = self.client.get_game_player_advanced_stats(
            season=self.season,
            game_ids=game_ids,
            player_ids=player_ids,
            dates=dates,
            per_page=self.config.get("per_page", 100),
        )

        for stat in stats:
            game_date = (stat.get("game", {}).get("date") or date or "")[:10]
            stat["season_type"] = get_season_type_for_date(game_date, self.season)

        return {
            "season": self.season,
            "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "total_stats": len(stats),
            "stats": stats,
        }

    def extract_and_save(self, **kwargs) -> List[str]:
        """
        Extrai dados por data, salvando um arquivo NDJSON por data com stats.
        Usa o helper compartilhado da BaseExtractor (range + contagem de falhas).

        Returns:
            Lista de caminhos completos dos arquivos salvos no GCS
        """
        return self.extract_and_save_by_date(count_key="stats", **kwargs)
