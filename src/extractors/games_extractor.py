"""Extractor para jogos."""
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
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
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extrai dados de jogos.
        
        Args:
            team_ids: Lista de IDs de times (opcional)
            date: Data no formato YYYY-MM-DD (opcional)
        
        Returns:
            Dicionário com os dados extraídos
        
        Note:
            Se nenhum filtro for fornecido, usa a temporada (season) como filtro padrão.
        """
        dates = [date] if date else None
        
        logger.info(f"Extraindo jogos da temporada {self.season}...")
        logger.info(f"Filtro por temporada: {self.season}")
        if date:
            logger.info(f"Filtro adicional por data: {date}")
        if team_ids:
            logger.info(f"Filtro adicional por team_ids: {team_ids}")
        
        games = self.client.get_games(
            season=self.season,
            team_ids=team_ids,
            dates=dates,
            per_page=self.config.get("per_page", 100),
        )
        
        # Remove o campo status de cada jogo para evitar problemas de inferência de tipo no BigQuery
        # Cria uma nova lista com cópias dos jogos sem o campo status
        games_without_status = []
        for game in games:
            if isinstance(game, dict):
                # Cria uma cópia do dicionário sem o campo status
                game_copy = {k: v for k, v in game.items() if k != "status"}
                games_without_status.append(game_copy)
            else:
                games_without_status.append(game)
        
        return {
            "season": self.season,
            "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "total_games": len(games_without_status),
            "games": games_without_status,
        }
    
    def extract_and_save(self, **kwargs) -> List[str]:
        """
        Extrai dados por data, fazendo requisições filtradas e salvando imediatamente.
        Usa automaticamente o range da temporada (helper compartilhado na BaseExtractor,
        que também unifica o end_date em NBA_SEASON_END_DATES + fallback hoje).

        Args:
            **kwargs: Outros parâmetros (team_ids, etc.)

        Returns:
            Lista de caminhos completos dos arquivos salvos no GCS
        """
        return self.extract_and_save_by_date(count_key="games", **kwargs)
