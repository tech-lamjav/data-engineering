"""Extractor para estatísticas de jogadores por jogo por período (quarto)."""
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from src.extractors.base_extractor import BaseExtractor
from src.config import NBA_SEASON_END_DATES, get_season_type_for_date
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class GamePlayerStatsPeriodExtractor(BaseExtractor):
    """Extractor para estatísticas de jogadores por jogo, filtradas por período/quarto."""

    def __init__(self, season: int = None):
        """Inicializa o extractor de estatísticas por período."""
        super().__init__("game_player_stats_period", season)

    def extract(
        self,
        date: Optional[str] = None,
        period: Optional[int] = None,
        game_ids: Optional[List[int]] = None,
        player_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Extrai estatísticas de jogadores por jogo para uma data e período específicos.

        Args:
            date: Data no formato YYYY-MM-DD (opcional)
            period: Número do período/quarto 1-4 (opcional)
            game_ids: Lista de IDs de jogos (opcional)
            player_ids: Lista de IDs de jogadores (opcional)

        Returns:
            Dicionário com os dados extraídos
        """
        dates = [date] if date else None

        logger.info(f"Extraindo estatísticas por período para temporada {self.season}...")
        if date:
            logger.info(f"Filtro por data: {date}")
        if period:
            logger.info(f"Filtro por período: Q{period}")

        stats = self.client.get_game_player_stats(
            season=self.season,
            game_ids=game_ids,
            player_ids=player_ids,
            dates=dates,
            per_page=self.config.get("per_page", 100),
            period=period,
        )

        for stat in stats:
            game_date = (stat.get("game", {}).get("date") or date or "")[:10]
            stat["season_type"] = get_season_type_for_date(game_date, self.season)

        return {
            "season": self.season,
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "period": period,
            "total_stats": len(stats),
            "stats": stats,
        }

    def save_to_gcs(
        self,
        data: Dict[str, Any],
        date: Optional[str] = None,
        period: Optional[int] = None,
    ) -> str:
        """
        Salva dados no GCS com path incluindo o período (q1, q2, q3, q4).

        Args:
            data: Dados a serem salvos
            date: Data no formato YYYY-MM-DD (opcional)
            period: Número do período/quarto (opcional)

        Returns:
            Caminho completo do arquivo no GCS
        """
        return self.storage.upload_json(
            data=data,
            endpoint=self.endpoint_name,
            season=self.season,
            date=date,
            period=period,
        )

    def _get_season_date_range(self) -> List[str]:
        """
        Gera lista de datas da temporada NBA (outubro do mesmo ano até a data de hoje).

        Returns:
            Lista de datas no formato YYYY-MM-DD
        """
        start_date = datetime(self.season, 10, 21)
        end_str = NBA_SEASON_END_DATES.get(self.season)
        end_date = datetime.strptime(end_str, "%Y-%m-%d") if end_str else datetime.now()

        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        return dates

    def extract_and_save(self, **kwargs) -> List[str]:
        """
        Extrai dados por data × período, salvando um arquivo por combinação.
        Skipa combinações sem stats para não criar arquivos vazios.

        Returns:
            Lista de caminhos completos dos arquivos salvos no GCS
        """
        logger.info(f"Iniciando extração do endpoint: {self.endpoint_name}")

        periods = self.config.get("periods", [1, 2, 3, 4])
        dates = self._get_season_date_range()
        logger.info(f"Processando {len(dates)} datas × {len(periods)} períodos")

        saved_paths = []
        skipped = []

        for game_date in dates:
            for period in periods:
                try:
                    logger.info("=" * 60)
                    logger.info(f"Processando data: {game_date}, período: Q{period}")

                    data = self.extract(
                        date=game_date,
                        period=period,
                        **{k: v for k, v in kwargs.items() if k not in ("date", "period")}
                    )
                    stats = data.get("stats", [])

                    if not stats:
                        logger.info(
                            f"Nenhuma estatística para {game_date} Q{period}. Pulando..."
                        )
                        skipped.append((game_date, period))
                        continue

                    logger.info(
                        f"Coletadas {len(stats)} estatísticas para {game_date} Q{period}"
                    )

                    gcs_path = self.save_to_gcs(data, date=game_date, period=period)
                    saved_paths.append(gcs_path)
                    logger.info(f"Arquivo salvo: {gcs_path}")

                except Exception as e:
                    logger.error(
                        f"Erro ao processar {game_date} Q{period}: {str(e)}",
                        exc_info=True,
                    )
                    continue

        logger.info("=" * 60)
        logger.info(f"Extração concluída: {len(saved_paths)} arquivo(s) salvo(s)")
        if skipped:
            logger.info(f"Combinações sem dados: {len(skipped)}")

        return saved_paths
