"""Gerenciamento de external tables no BigQuery."""
from typing import Optional, List, Dict
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
from src.config import (
    GCS_BUCKET_NAME,
    BIGQUERY_PROJECT_ID,
    BIGQUERY_DATASET,
    BIGQUERY_LOCATION,
    ENDPOINT_CONFIGS,
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class BigQueryClient:
    """Classe para gerenciar external tables no BigQuery."""
    
    def __init__(self, project_id: Optional[str] = None, dataset_id: Optional[str] = None):
        """
        Inicializa o cliente do BigQuery.
        
        Args:
            project_id: ID do projeto (usa BIGQUERY_PROJECT_ID do config se None)
            dataset_id: ID do dataset (usa BIGQUERY_DATASET do config se None)
        """
        self.project_id = project_id or BIGQUERY_PROJECT_ID
        self.dataset_id = dataset_id or BIGQUERY_DATASET
        
        if not self.project_id:
            raise ValueError("BIGQUERY_PROJECT_ID não está configurado")
        
        # Inicializa cliente do BigQuery
        self.client = bigquery.Client(project=self.project_id)
        logger.info(f"Cliente BigQuery inicializado para projeto: {self.project_id}")
    
    def get_external_table_uri(self, endpoint: str, season: int, has_date: bool = False, category: str = None, type: str = None) -> str:
        """
        Gera o URI do GCS para a external table.
        
        Args:
            endpoint: Nome do endpoint
            season: Temporada
            has_date: Se o endpoint tem data (usa wildcard)
            category: Categoria para season_averages (opcional)
            type: Tipo para season_averages (opcional)
        
        Returns:
            URI do GCS (gs://bucket/path)
        """
        bucket = GCS_BUCKET_NAME
        if has_date:
            # Para endpoints com data, usa wildcard para ler todos os arquivos
            uri = f"gs://{bucket}/nba/{endpoint}/{season}/*.json"
        elif endpoint == "season_averages" and category and type:
            # Para season_averages, cria URI específico para cada combinação category-type
            # Formato: raw_nba_season_averages_{season}-{category}-{type}.json
            uri = f"gs://{bucket}/nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{category}-{type}.json"
        else:
            # Para endpoints sem data, lê um arquivo específico
            uri = f"gs://{bucket}/nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}.json"
        
        return uri
    
    def create_dataset_if_not_exists(self) -> bigquery.Dataset:
        """
        Cria o dataset se não existir.
        
        Returns:
            Objeto Dataset criado ou existente
        """
        dataset_ref = self.client.dataset(self.dataset_id)
        
        try:
            dataset = self.client.get_dataset(dataset_ref)
            logger.info(f"Dataset {self.dataset_id} já existe")
            return dataset
        except NotFound:
            dataset = bigquery.Dataset(dataset_ref)
            dataset.location = BIGQUERY_LOCATION
            dataset.description = "Dataset para external tables dos dados brutos da NBA"
            dataset = self.client.create_dataset(dataset)
            logger.info(f"✓ Dataset {self.dataset_id} criado na localização {BIGQUERY_LOCATION}")
            return dataset
    
    def create_external_table(
        self,
        table_id: str,
        uri: str,
        description: str = "",
    ) -> bigquery.Table:
        """
        Cria ou atualiza uma external table no BigQuery.
        Sempre usa autodetect para inferir o schema automaticamente.
        
        Args:
            table_id: ID da tabela
            uri: URI do GCS (gs://bucket/path)
            description: Descrição da tabela
        
        Returns:
            Objeto Table criado ou atualizado
        """
        dataset_ref = self.client.dataset(self.dataset_id)
        table_ref = dataset_ref.table(table_id)
        
        # Verifica se a tabela já existe
        try:
            existing_table = self.client.get_table(table_ref)
            logger.warning(f"Tabela {self.dataset_id}.{table_id} já existe. Atualizando...")
            # Atualiza a tabela existente
            external_config = bigquery.ExternalConfig(bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
            external_config.source_uris = [uri]
            external_config.autodetect = True
            # Ignora erros de parsing para arquivos JSON com múltiplas linhas
            external_config.ignore_unknown_values = True
            existing_table.external_data_configuration = external_config
            existing_table.description = description
            table = self.client.update_table(
                existing_table, 
                ["external_data_configuration", "description"]
            )
            logger.info(f"✓ Tabela {self.dataset_id}.{table_id} atualizada")
            return table
        except NotFound:
            # Cria nova tabela
            table = bigquery.Table(table_ref)
            external_config = bigquery.ExternalConfig(bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
            external_config.source_uris = [uri]
            external_config.autodetect = True
            # Ignora erros de parsing para arquivos JSON com múltiplas linhas
            external_config.ignore_unknown_values = True
            table.external_data_configuration = external_config
            table.description = description
            
            table = self.client.create_table(table)
            logger.info(f"✓ Tabela {self.dataset_id}.{table_id} criada")
            return table
    
    def create_all_external_tables(
        self,
        season: int,
        endpoints: Optional[List[str]] = None,
    ) -> Dict[str, bool]:
        """
        Cria todas as external tables para os endpoints configurados.
        
        Args:
            season: Temporada
            endpoints: Lista de endpoints para criar (cria todos se None)
        
        Returns:
            Dicionário com status de criação de cada tabela
        """
        # Cria dataset se não existir
        self.create_dataset_if_not_exists()
        
        # Endpoints para processar
        endpoints_to_process = endpoints or list(ENDPOINT_CONFIGS.keys())
        
        results = {}
        
        # Combinações de season_averages
        SEASON_AVERAGES_COMBINATIONS = [
            {"category": "general", "type": "base"},
            {"category": "general", "type": "advanced"},
            {"category": "shooting", "type": "by_zone"},
        ]
        
        for endpoint in endpoints_to_process:
            if endpoint not in ENDPOINT_CONFIGS:
                logger.warning(f"Endpoint {endpoint} não encontrado na configuração. Pulando...")
                results[endpoint] = False
                continue
            
            try:
                config = ENDPOINT_CONFIGS[endpoint]
                has_date = config.get("has_date", False)
                
                # Tratamento especial para season_averages: cria 3 external tables
                if endpoint == "season_averages":
                    for combo in SEASON_AVERAGES_COMBINATIONS:
                        category = combo["category"]
                        type_param = combo["type"]
                        
                        # Gera URI do GCS
                        uri = self.get_external_table_uri(
                            endpoint, 
                            season, 
                            has_date, 
                            category=category, 
                            type=type_param
                        )
                        
                        # Nome da tabela específico para cada combinação
                        table_id = f"raw_{endpoint}_{category}_{type_param}"
                        
                        # Descrição
                        description = f"External table para dados brutos de season_averages (category={category}, type={type_param})"
                        
                        # Cria a tabela
                        self.create_external_table(
                            table_id=table_id,
                            uri=uri,
                            description=description,
                        )
                        
                        result_key = f"{endpoint}_{category}_{type_param}"
                        results[result_key] = True
                        logger.info(f"✓ External table criada: {self.dataset_id}.{table_id}")
                        logger.info(f"  URI: {uri}")
                else:
                    # Gera URI do GCS
                    uri = self.get_external_table_uri(endpoint, season, has_date)
                    
                    # Nome da tabela (sem o ano)
                    table_id = f"raw_{endpoint}"
                    
                    # Descrição
                    description = f"External table para dados brutos do endpoint {endpoint}"
                    if has_date:
                        description += " (inclui múltiplos arquivos por data)"
                    
                    # Cria a tabela
                    self.create_external_table(
                        table_id=table_id,
                        uri=uri,
                        description=description,
                    )
                    
                    results[endpoint] = True
                    logger.info(f"✓ External table criada: {self.dataset_id}.{table_id}")
                    logger.info(f"  URI: {uri}")
                
            except Exception as e:
                logger.error(f"✗ Erro ao criar external table para {endpoint}: {str(e)}", exc_info=True)
                if endpoint == "season_averages":
                    # Marca todas as combinações como falha
                    for combo in SEASON_AVERAGES_COMBINATIONS:
                        result_key = f"{endpoint}_{combo['category']}_{combo['type']}"
                        results[result_key] = False
                else:
                    results[endpoint] = False
        
        return results
