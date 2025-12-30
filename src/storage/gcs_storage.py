"""Gerenciamento de uploads no Google Cloud Storage."""
import json
from typing import Dict, Any, Optional
from google.cloud import storage
from google.api_core import exceptions
from src.config import GCS_BUCKET_NAME, GCP_PROJECT_ID, GCS_USE_ADC, get_gcs_path
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class GCSStorage:
    """Classe para gerenciar uploads no Google Cloud Storage."""
    
    def __init__(self, bucket_name: Optional[str] = None):
        """
        Inicializa o cliente do GCS.
        
        Args:
            bucket_name: Nome do bucket (usa config se None)
        """
        self.bucket_name = bucket_name or GCS_BUCKET_NAME
        
        # Inicializa cliente do GCS
        # Usa Application Default Credentials (ADC) se GCS_USE_ADC for True
        if GCS_USE_ADC:
            # Se project_id estiver definido, usa explicitamente
            if GCP_PROJECT_ID:
                self.client = storage.Client(project=GCP_PROJECT_ID)
            else:
                self.client = storage.Client()
        else:
            # Alternativa: usar service account key file
            # self.client = storage.Client.from_service_account_json(
            #     os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            # )
            if GCP_PROJECT_ID:
                self.client = storage.Client(project=GCP_PROJECT_ID)
            else:
                self.client = storage.Client()
        
        # Obtém ou cria o bucket
        try:
            self.bucket = self.client.bucket(self.bucket_name)
            # Verifica se o bucket existe
            if not self.bucket.exists():
                logger.info(f"Criando bucket {self.bucket_name}...")
                self.bucket = self.client.create_bucket(self.bucket_name)
                logger.info(f"Bucket {self.bucket_name} criado com sucesso")
            else:
                logger.info(f"Bucket {self.bucket_name} já existe")
        except exceptions.Forbidden:
            logger.error(f"Sem permissão para acessar/criar o bucket {self.bucket_name}")
            raise
        except Exception as e:
            logger.error(f"Erro ao acessar bucket {self.bucket_name}: {str(e)}")
            raise
    
    def upload_json(
        self,
        data: Dict[str, Any],
        endpoint: str,
        season: int,
        date: Optional[str] = None,
    ) -> str:
        """
        Faz upload de um JSON para o GCS.
        
        Args:
            data: Dados a serem enviados (será convertido para JSON)
            endpoint: Nome do endpoint
            season: Ano da temporada
            date: Data no formato YYYY-MM-DD (opcional)
        
        Returns:
            Caminho completo do arquivo no GCS
        """
        # Gera o caminho no GCS
        blob_path = get_gcs_path(endpoint, season, date)
        logger.info(f"Fazendo upload para: gs://{self.bucket_name}/{blob_path}")
        
        # Converte dados para JSON
        json_data = json.dumps(data, indent=2, ensure_ascii=False)
        
        # Faz upload
        try:
            blob = self.bucket.blob(blob_path)
            blob.upload_from_string(
                json_data,
                content_type="application/json",
            )
            
            # Verifica se o arquivo foi criado corretamente
            if blob.exists():
                logger.info(
                    f"✓ Upload realizado com sucesso: gs://{self.bucket_name}/{blob_path} "
                    f"({len(json_data)} bytes)"
                )
                logger.info(f"  Estrutura de pastas criada automaticamente: {blob_path}")
            else:
                logger.warning(f"Upload concluído mas arquivo não encontrado: {blob_path}")
            
            return f"gs://{self.bucket_name}/{blob_path}"
            
        except Exception as e:
            logger.error(f"Erro ao fazer upload para {blob_path}: {str(e)}")
            raise
    
    def upload_file(
        self,
        file_path: str,
        blob_path: str,
    ) -> str:
        """
        Faz upload de um arquivo local para o GCS.
        
        Args:
            file_path: Caminho do arquivo local
            blob_path: Caminho no GCS
        
        Returns:
            Caminho completo do arquivo no GCS
        """
        try:
            blob = self.bucket.blob(blob_path)
            blob.upload_from_filename(file_path)
            
            logger.info(
                f"Upload realizado com sucesso: gs://{self.bucket_name}/{blob_path}"
            )
            
            return f"gs://{self.bucket_name}/{blob_path}"
            
        except Exception as e:
            logger.error(f"Erro ao fazer upload de {file_path} para {blob_path}: {str(e)}")
            raise
    
    def file_exists(
        self,
        endpoint: str,
        season: int,
        date: Optional[str] = None,
    ) -> bool:
        """
        Verifica se um arquivo já existe no GCS.
        
        Args:
            endpoint: Nome do endpoint
            season: Ano da temporada
            date: Data no formato YYYY-MM-DD (opcional)
        
        Returns:
            True se o arquivo existe
        """
        blob_path = get_gcs_path(endpoint, season, date)
        blob = self.bucket.blob(blob_path)
        return blob.exists()

