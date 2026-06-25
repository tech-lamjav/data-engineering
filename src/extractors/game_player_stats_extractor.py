"""Extractor para estatísticas de jogadores por jogo."""
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from src.extractors.base_extractor import BaseExtractor
from src.config import NBA_SEASON_END_DATES, get_season_type_for_date
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
    
    def _get_season_date_range(self) -> List[str]:
        """
        Gera lista de datas da temporada NBA (outubro do mesmo ano até a data de hoje).
        
        Returns:
            Lista de datas no formato YYYY-MM-DD
        """
        start_date = datetime(self.season, 10, 21)
        end_str = NBA_SEASON_END_DATES.get(self.season)
        end_date = datetime.strptime(end_str, "%Y-%m-%d") if end_str else datetime.now(timezone.utc).replace(tzinfo=None)
        
        dates = []
        current = start_date
        while current <= end_date:
            # Pula finais de semana? Não, vamos incluir todos os dias
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
            **kwargs: Outros parâmetros (game_ids, player_ids, etc.)
        
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
                
                # Extrai estatísticas apenas para esta data
                data = self.extract(
                    date=game_date,
                    **{k: v for k, v in kwargs.items() if k != "date"}
                )
                stats = data.get("stats", [])
                
                if not stats:
                    logger.warning(f"Nenhuma estatística encontrada para {game_date}. Pulando...")
                    skipped_dates.append(game_date)
                    continue
                
                logger.info(f"Coletadas {len(stats)} estatísticas para {game_date}")
                
                # Salva imediatamente
                date_data = {
                    "season": self.season,
                    "date": game_date,
                    "total_stats": len(stats),
                    "stats": stats,
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
