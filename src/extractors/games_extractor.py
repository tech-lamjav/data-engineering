"""Extractor para jogos."""
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
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
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "total_games": len(games_without_status),
            "games": games_without_status,
        }
    
    def _get_season_date_range(self) -> List[str]:
        """
        Gera lista de datas da temporada NBA (outubro do mesmo ano até junho do ano seguinte).
        
        Returns:
            Lista de datas no formato YYYY-MM-DD
        """
        # Temporada NBA vai de outubro do mesmo ano até abril do ano seguinte
        # Ex: temporada 2025 = outubro 2025 até hoje
        start_date = datetime(self.season, 10, 21)  # Outubro do mesmo ano
        end_date = datetime(self.season + 1, 6, 30)  # Junho do ano seguinte
        
        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        
        return dates
    
    def extract_and_save(
        self,
        **kwargs
    ) -> List[str]:
        """
        Extrai dados por data, fazendo requisições filtradas e salvando imediatamente.
        Usa automaticamente o range de datas da temporada.
        
        Args:
            **kwargs: Outros parâmetros (team_ids, etc.)
        
        Returns:
            Lista de caminhos completos dos arquivos salvos no GCS
        """
        logger.info(f"Iniciando extração do endpoint: {self.endpoint_name}")
        
        # Usa range padrão da temporada (definido no código)
        logger.info(f"Gerando range de datas para temporada {self.season}...")
        dates = self._get_season_date_range()
        logger.info(f"Processando {len(dates)} datas da temporada")
        
        saved_paths = []
        skipped_dates = []
        
        # Processa cada data individualmente
        for game_date in dates:
            try:
                logger.info(f"=" * 60)
                logger.info(f"Processando data: {game_date}")
                
                # Extrai jogos apenas para esta data
                data = self.extract(
                    date=game_date,
                    **{k: v for k, v in kwargs.items() if k != "date" and k != "dates"}
                )
                games = data.get("games", [])
                
                if not games:
                    logger.warning(f"Nenhum jogo encontrado para {game_date}. Pulando...")
                    skipped_dates.append(game_date)
                    continue
                
                logger.info(f"Coletados {len(games)} jogos para {game_date}")
                
                # Salva imediatamente
                date_data = {
                    "season": self.season,
                    "date": game_date,
                    "total_games": len(games),
                    "games": games,
                }
                
                gcs_path = self.save_to_gcs(date_data, date=game_date)
                saved_paths.append(gcs_path)
                logger.info(f"✓ Arquivo salvo: {gcs_path}")
                
            except Exception as e:
                logger.error(f"Erro ao processar data {game_date}: {str(e)}", exc_info=True)
                continue
        
        logger.info(f"=" * 60)
        logger.info(f"Extração concluída: {len(saved_paths)} arquivo(s) salvo(s)")
        if skipped_dates:
            logger.info(f"Datas sem dados: {len(skipped_dates)} ({skipped_dates[:5]}...)")
        if not saved_paths:
            logger.warning(
                f"Nenhum arquivo salvo para endpoint: {self.endpoint_name}. "
                "Verifique se há jogos na temporada ou se a API está respondendo."
            )

        return saved_paths
