"""Extractor para estatísticas de jogadores por jogo."""
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from src.extractors.base_extractor import BaseExtractor
from src.config import get_season_type_for_date
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class GamePlayerStatsExtractor(BaseExtractor):
    """Extractor para estatísticas de jogadores por jogo."""
    
    def __init__(self, season: int = None):
        """Inicializa o extractor de estatísticas."""
        super().__init__("game_player_stats", season)
    
    def extract(
        self,
        game_ids: Optional[List[int]] = None,
        player_ids: Optional[List[int]] = None,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extrai estatísticas de jogadores por jogo.
        
        Args:
            game_ids: Lista de IDs de jogos (opcional)
            player_ids: Lista de IDs de jogadores (opcional)
            date: Data no formato YYYY-MM-DD (opcional)
        
        Returns:
            Dicionário com os dados extraídos
        
        Note:
            Se nenhum filtro for fornecido, usa a temporada (season) como filtro padrão.
        """
        dates = [date] if date else None
        
        logger.info(f"Extraindo estatísticas de jogadores por jogo para temporada {self.season}...")
        logger.info(f"Filtro por temporada: {self.season}")
        if date:
            logger.info(f"Filtro adicional por data: {date}")
        if game_ids:
            logger.info(f"Filtro adicional por game_ids: {game_ids}")
        if player_ids:
            logger.info(f"Filtro adicional por player_ids: {player_ids}")
        
        stats = self.client.get_game_player_stats(
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
        Extrai dados por data, fazendo requisições filtradas e salvando imediatamente.
        Usa automaticamente o range da temporada (helper compartilhado na BaseExtractor).

        Args:
            **kwargs: Outros parâmetros (game_ids, player_ids, etc.)

        Returns:
            Lista de caminhos completos dos arquivos salvos no GCS
        """
        return self.extract_and_save_by_date(count_key="stats", **kwargs)
