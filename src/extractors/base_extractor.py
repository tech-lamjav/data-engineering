"""Classe base para extractors."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from src.clients.balldontlie_client import BallDontLieClient
from src.storage.gcs_storage import GCSStorage
from src.config import SEASON, ENDPOINT_CONFIGS, NBA_SEASON_END_DATES
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class BaseExtractor(ABC):
    """Classe base para extractors de dados."""

    def __init__(
        self,
        endpoint_name: str,
        season: int = SEASON,
        client: Optional[Any] = None,
        storage: Optional[GCSStorage] = None,
        sport: str = "nba",
    ):
        """
        Inicializa o extractor.

        Args:
            endpoint_name: Nome do endpoint
            season: Ano da temporada
            client: Client de API (default: BallDontLieClient — NBA)
            storage: Storage backend (default: GCSStorage)
            sport: Identificador do esporte para path no GCS ("nba", "futebol")
        """
        self.endpoint_name = endpoint_name
        self.season = season
        self.client = client or BallDontLieClient()
        self.storage = storage or GCSStorage()
        self.sport = sport
        self.config = ENDPOINT_CONFIGS.get(endpoint_name, {})
        self.has_date = self.config.get("has_date", False)
    
    @abstractmethod
    def extract(self, **kwargs) -> Dict[str, Any]:
        """
        Extrai dados do endpoint.
        
        Args:
            **kwargs: Parâmetros específicos do endpoint
        
        Returns:
            Dicionário com os dados extraídos
        """
        pass
    
    def save_to_gcs(
        self,
        data: Dict[str, Any],
        date: Optional[str] = None,
    ) -> str:
        """
        Salva dados no GCS.
        
        Args:
            data: Dados a serem salvos
            date: Data no formato YYYY-MM-DD (opcional)
        
        Returns:
            Caminho completo do arquivo no GCS
        """
        return self.storage.upload_json(
            data=data,
            endpoint=self.endpoint_name,
            season=self.season,
            date=date,
            sport=self.sport,
        )
    
    def extract_and_save(self, **kwargs) -> str:
        """
        Extrai dados e salva no GCS.

        Usado apenas pelos endpoints latest-only (active_players, player_injuries,
        team_standings, season_averages). Os endpoints por data (games, stats, props,
        odds) sobrescrevem este método com a variante por data/jogo. Por isso o ramo
        antigo de `self.has_date` aqui era dead code e foi removido.

        Args:
            **kwargs: Parâmetros específicos do endpoint

        Returns:
            Caminho completo do arquivo no GCS
        """
        logger.info(f"Iniciando extração do endpoint: {self.endpoint_name}")

        data = self.extract(**kwargs)

        record_count = len(data) if isinstance(data, list) else len(data.get("data", []))
        if record_count == 0:
            logger.warning(
                f"Extração retornou 0 registros para endpoint: {self.endpoint_name}"
            )

        # Salva no GCS
        gcs_path = self.save_to_gcs(data)

        logger.info(f"Extração concluída: {gcs_path}")
        return gcs_path

    # ------------------------------------------------------------------
    # Helpers compartilhados pelos extractors NBA por data
    # (games, game_player_stats, game_player_stats_period, game_player_advanced_stats)
    # ------------------------------------------------------------------
    def _get_season_date_range(self) -> List[str]:
        """
        Gera a lista de datas da temporada NBA (21/out/{season} até o fim).

        O fim é NBA_SEASON_END_DATES[season] para temporadas encerradas, ou hoje
        (UTC) para a temporada corrente. Fonte única para os 4 extractors por data
        — antes cada um duplicava esta lógica e o de `games` chegou a divergir usando
        um end_date hardcoded (30/jun), causando range diferente dos demais.

        Returns:
            Lista de datas no formato YYYY-MM-DD.
        """
        start_date = datetime(self.season, 10, 21)
        end_str = NBA_SEASON_END_DATES.get(self.season)
        end_date = (
            datetime.strptime(end_str, "%Y-%m-%d")
            if end_str
            else datetime.now(timezone.utc).replace(tzinfo=None)
        )

        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        return dates

    def extract_and_save_by_date(
        self,
        count_key: str,
        **kwargs,
    ) -> List[str]:
        """
        Itera o range da temporada, extrai 1 requisição filtrada por data e salva
        imediatamente. Centraliza o loop comum dos extractors NBA por data.

        Diferencia 'data sem dados' (não conta como falha) de 'data falhou'
        (exceção): acumula `failed` e, quando > 0, emite um ERROR de RESUMO distinto
        do log de 'datas sem dados', para o orquestrador/resumo diário não confundir
        'temporada sem jogos' com 'metade das datas falhou'.

        Args:
            count_key: chave do array no dict retornado por extract() (ex.: "games",
                "stats") usada para decidir se a data tem dados.
            **kwargs: parâmetros extras repassados a extract() (team_ids, etc.).

        Returns:
            Lista de caminhos completos dos arquivos salvos no GCS.
        """
        logger.info(f"Iniciando extração do endpoint: {self.endpoint_name}")

        logger.info(f"Gerando range de datas para temporada {self.season}...")
        dates = self._get_season_date_range()
        logger.info(f"Processando {len(dates)} datas da temporada")

        saved_paths: List[str] = []
        skipped_dates: List[str] = []
        failed_dates: List[str] = []

        forwarded = {k: v for k, v in kwargs.items() if k not in ("date", "dates")}

        for game_date in dates:
            try:
                logger.info("=" * 60)
                logger.info(f"Processando data: {game_date}")

                data = self.extract(date=game_date, **forwarded)
                records = data.get(count_key, [])

                if not records:
                    logger.warning(
                        f"Nenhum dado encontrado para {game_date}. Pulando..."
                    )
                    skipped_dates.append(game_date)
                    continue

                logger.info(f"Coletados {len(records)} registros para {game_date}")

                gcs_path = self.save_to_gcs(data, date=game_date)
                saved_paths.append(gcs_path)
                logger.info(f"✓ Arquivo salvo: {gcs_path}")

            except Exception as e:
                logger.error(
                    f"Erro ao processar data {game_date}: {str(e)}", exc_info=True
                )
                failed_dates.append(game_date)
                continue

        logger.info("=" * 60)
        logger.info(f"Extração concluída: {len(saved_paths)} arquivo(s) salvo(s)")
        if skipped_dates:
            logger.info(
                f"Datas sem dados: {len(skipped_dates)} ({skipped_dates[:5]}...)"
            )
        if failed_dates:
            # Resumo de FALHA distinto de 'datas sem dados': sinaliza ao orquestrador
            # que houve erro real (timeout/5xx/JSON/GCS), não temporada vazia.
            logger.error(
                f"RESUMO DE FALHA — {self.endpoint_name}: {len(failed_dates)} data(s) "
                f"falharam de {len(dates)} ({failed_dates[:5]}...). "
                "Dados podem estar incompletos — re-executar."
            )
        elif not saved_paths:
            logger.warning(
                f"Nenhum arquivo salvo para endpoint: {self.endpoint_name}. "
                "Verifique se há jogos na temporada ou se a API está respondendo."
            )

        return saved_paths
