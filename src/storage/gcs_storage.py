"""Gerenciamento de uploads no Google Cloud Storage."""
import json
from typing import Dict, Any, Optional, List
from google.cloud import storage
from google.api_core import exceptions
from src.config import GCS_BUCKET_NAME, GCP_PROJECT_ID, GCS_USE_ADC, get_gcs_path
from src.utils.logger import setup_logger
from src.utils.helpers import normalize_dict_keys

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
        market: Optional[str] = None,
        game_id: Optional[int] = None,
        category: Optional[str] = None,
        type: Optional[str] = None,
    ) -> str:
        """
        Faz upload de um JSON para o GCS.
        
        Args:
            data: Dados a serem enviados (será convertido para JSON)
            endpoint: Nome do endpoint
            season: Ano da temporada
            date: Data no formato YYYY-MM-DD (opcional)
            market: Market para player_props (opcional)
            game_id: Game ID para player_props (opcional)
            category: Categoria para season_averages (opcional)
            type: Tipo para season_averages (opcional)
        
        Returns:
            Caminho completo do arquivo no GCS
        """
        # Gera o caminho no GCS
        blob_path = get_gcs_path(
            endpoint, season, date=date, market=market, game_id=game_id, 
            category=category, type=type
        )
        logger.info(f"Fazendo upload para: gs://{self.bucket_name}/{blob_path}")
        
        # Normaliza nomes de colunas para compatibilidade com BigQuery
        # Remove caracteres inválidos (parênteses, hífens, etc.)
        data = normalize_dict_keys(data)
        
        # Converte dados para NEWLINE_DELIMITED_JSON (um objeto JSON por linha)
        # Necessário para compatibilidade com BigQuery
        json_data = self._convert_to_newline_delimited_json(data, endpoint)
        
        # Valida que o JSON gerado é válido
        if not json_data or not json_data.strip():
            raise ValueError(f"JSON gerado está vazio para {blob_path}")
        
        # Valida que cada linha é um JSON válido (para NDJSON)
        lines = json_data.strip().split('\n')
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"JSON inválido na linha {line_num} do arquivo {blob_path}: {str(e)}"
                )
        
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
    
    def _convert_to_newline_delimited_json(self, data: Dict[str, Any], endpoint: str) -> str:
        """
        Converte dados para formato NEWLINE_DELIMITED_JSON.
        Se os dados contiverem um array, cada item do array será uma linha.
        
        Args:
            data: Dados a serem convertidos
            endpoint: Nome do endpoint (para lógica específica)
        
        Returns:
            String JSON no formato NEWLINE_DELIMITED_JSON
        """
        # Para season_averages, expande o array 'averages' em linhas separadas
        if endpoint == "season_averages" and "averages" in data:
            lines = []
            # Metadados que serão incluídos em cada linha
            metadata = {
                "season": data.get("season"),
                "category": data.get("category"),
                "type": data.get("type"),
                "season_type": data.get("season_type"),
            }
            
            # Cria uma linha para cada item do array, incluindo os metadados
            for item in data["averages"]:
                # Combina metadados com o item
                combined = {**metadata, **item}
                lines.append(json.dumps(combined, ensure_ascii=False))
            
            return "\n".join(lines)
        
        # Para team_season_averages, expande o array 'team_averages' em linhas separadas
        if endpoint == "team_season_averages" and "team_averages" in data:
            lines = []
            metadata = {
                "season": data.get("season"),
                "category": data.get("category"),
                "type": data.get("type"),
                "season_type": data.get("season_type"),
            }
            for item in data["team_averages"]:
                combined = {**metadata, **item}
                lines.append(json.dumps(combined, ensure_ascii=False))
            return "\n".join(lines) if lines else json.dumps(metadata, ensure_ascii=False)
        
        # Para player_props, expande o array 'props' em linhas separadas
        # Cada prop será uma linha com os metadados (season, game_id, vendor, etc.)
        if endpoint == "player_props" and "props" in data:
            lines = []
            # Metadados que serão incluídos em cada linha
            metadata = {
                "season": data.get("season"),
                "game_id": data.get("game_id"),
                "vendor": data.get("vendor"),
                "total_props": data.get("total_props"),
            }
            
            # Remove campos None do metadata
            metadata = {k: v for k, v in metadata.items() if v is not None}
            
            # Se não houver props, retorna um objeto vazio (evita arquivo vazio)
            if not data.get("props") or len(data["props"]) == 0:
                # Retorna um objeto com apenas os metadados para indicar que não há props
                return json.dumps(metadata, ensure_ascii=False)
            
            # Cria uma linha para cada prop, incluindo os metadados
            for item in data["props"]:
                if isinstance(item, dict):
                    # Combina metadados com o item da prop
                    combined = {**metadata, **item}
                    lines.append(json.dumps(combined, ensure_ascii=False))
                else:
                    # Se o item não for um dict, adiciona como está com metadados
                    combined = {**metadata, "prop": item}
                    lines.append(json.dumps(combined, ensure_ascii=False))
            
            # Se não gerou nenhuma linha válida, retorna objeto com metadados
            if not lines:
                return json.dumps(metadata, ensure_ascii=False)
            
            return "\n".join(lines)
        
        # Para games e game_player_stats, salva como um único objeto JSON (mantém o array intacto)
        # Isso é necessário porque o BigQuery espera que cada linha seja um objeto completo com o array dentro
        if endpoint in ["games", "game_player_stats"]:
            return json.dumps(data, ensure_ascii=False)
        
        # Para outros endpoints, verifica se há arrays principais
        # Se houver um array no nível raiz ou um array com nome comum (games, players, etc.)
        array_keys = ["players", "teams", "injuries", "standings"]
        for key in array_keys:
            if key in data and isinstance(data[key], list):
                lines = []
                # Metadados (tudo exceto o array)
                metadata = {k: v for k, v in data.items() if k != key}
                
                # Se o array estiver vazio, retorna objeto com metadados apenas
                if not data[key]:
                    return json.dumps(metadata, ensure_ascii=False)
                
                # Cria uma linha para cada item do array
                for item in data[key]:
                    if isinstance(item, dict):
                        # Combina metadados com o item
                        combined = {**metadata, **item}
                        lines.append(json.dumps(combined, ensure_ascii=False))
                    else:
                        # Se não for dict, adiciona como está
                        combined = {**metadata, key: item}
                        lines.append(json.dumps(combined, ensure_ascii=False))
                
                # Se não gerou linhas válidas, retorna objeto com metadados
                if not lines:
                    return json.dumps(metadata, ensure_ascii=False)
                
                return "\n".join(lines)
        
        # Se não houver array, salva como um único objeto JSON (uma linha)
        return json.dumps(data, ensure_ascii=False)
    
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
    
    def get_game_ids_from_storage(self, season: int) -> List[int]:
        """
        Busca todos os game_ids dos arquivos de games salvos no GCS.
        Lista todos os arquivos na pasta nba/games/{season}/ e extrai game_ids.
        
        Args:
            season: Ano da temporada
        
        Returns:
            Lista de game_ids únicos
        """
        import json
        
        game_ids = set()
        
        # Prefixo da pasta de games para esta season
        prefix = f"nba/games/{season}/"
        
        logger.info(f"Listando arquivos em gs://{self.bucket_name}/{prefix}...")
        
        # Lista todos os blobs (arquivos) com este prefixo
        blobs = self.bucket.list_blobs(prefix=prefix)
        
        files_processed = 0
        
        for blob in blobs:
            # Ignora se não for arquivo JSON
            if not blob.name.endswith('.json'):
                continue
            
            try:
                # Baixa e lê o arquivo
                content = blob.download_as_text()
                
                games = []
                
                # Tenta primeiro como JSON único (formato padrão para games)
                # Formato esperado: {"season": 2025, "date": "...", "games": [...]}
                try:
                    data = json.loads(content)
                    if isinstance(data, dict) and "games" in data:
                        games = data.get("games", [])
                    elif isinstance(data, list):
                        # Se o JSON é um array direto de games
                        games = data
                    else:
                        logger.warning(f"Formato JSON inesperado em {blob.name}: não contém 'games' nem é um array")
                except json.JSONDecodeError as json_err:
                    # Se falhar, tenta como NDJSON (newline-delimited JSON)
                    # Cada linha é um objeto JSON separado
                    logger.debug(f"Tentando ler {blob.name} como NDJSON (newline-delimited)...")
                    lines = content.strip().split('\n')
                    for line_num, line in enumerate(lines, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            line_data = json.loads(line)
                            # Se a linha contém um array de games, extrai
                            if isinstance(line_data, dict) and "games" in line_data:
                                games.extend(line_data.get("games", []))
                            # Se a linha é um jogo diretamente (tem campo "id" e outros campos de jogo), adiciona
                            elif isinstance(line_data, dict) and "id" in line_data and "date" in line_data:
                                games.append(line_data)
                            # Se a linha é um array de games
                            elif isinstance(line_data, list):
                                games.extend(line_data)
                        except json.JSONDecodeError as line_err:
                            logger.warning(
                                f"Erro ao processar linha {line_num} do arquivo {blob.name}: {str(line_err)}"
                            )
                            continue
                
                # Extrai game_ids dos jogos encontrados
                games_found = 0
                for game in games:
                    if isinstance(game, dict):
                        game_id = game.get("id")
                        if game_id:
                            game_ids.add(game_id)
                            games_found += 1
                
                files_processed += 1
                logger.debug(f"Processado {blob.name}: {games_found} game_ids extraídos de {len(games)} jogos")
            except Exception as e:
                logger.warning(f"Erro ao processar arquivo {blob.name}: {str(e)}", exc_info=True)
                continue
        
        logger.info(f"Total de {files_processed} arquivos processados, {len(game_ids)} game_ids únicos encontrados")
        return sorted(list(game_ids))

