"""Classe base para extractors."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime
from src.clients.balldontlie_client import BallDontLieClient
from src.storage.gcs_storage import GCSStorage
from src.config import SEASON, ENDPOINT_CONFIGS
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

        # Determina a data se necessário
        date = None
        if self.has_date:
            date = kwargs.get("date")
            if not date:
                # Se não fornecida, usa data atual
                date = datetime.now().strftime("%Y-%m-%d")
        
        # Salva no GCS
        gcs_path = self.save_to_gcs(data, date=date)
        
        logger.info(f"Extração concluída: {gcs_path}")
        return gcs_path
