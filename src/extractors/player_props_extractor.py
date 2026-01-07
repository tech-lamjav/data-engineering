"""Extractor para props de jogadores."""
from typing import Dict, Any, Optional, List
from datetime import datetime
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class PlayerPropsExtractor(BaseExtractor):
    """Extractor para props de jogadores."""
    
    def __init__(self, season: int = None):
        """Inicializa o extractor de props."""
        super().__init__("player_props", season)
        self.market = self.config.get("market", "draftkings")
        # Lista de vendors suportados
        self.vendors = ["draftkings", "betway", "betrivers", "ballybet", "betparx", "caesars", "fanduel", "rebet"]
    
    def extract(
        self,
        game_id: int,
        player_id: Optional[int] = None,
        prop_type: Optional[str] = None,
        vendors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Extrai props de jogadores para um jogo específico.
        
        Args:
            game_id: ID do jogo (obrigatório)
            player_id: ID do jogador (opcional)
            prop_type: Tipo de prop (opcional)
            vendors: Lista de vendors (opcional, usa market se não fornecido)
        
        Returns:
            Dicionário com os dados extraídos
        """
        # Se vendors não fornecido, usa apenas o market configurado
        if not vendors:
            vendors = [self.market]
        
        logger.info(f"Extraindo props para game_id {game_id} (vendors: {vendors})...")
        
        props = self.client.get_player_props(
            game_id=game_id,
            player_id=player_id,
            prop_type=prop_type,
            vendors=vendors,
        )
        
        return {
            "season": self.season,
            "game_id": game_id,
            "vendors": vendors,
            "total_props": len(props),
            "props": props,
        }
    
    def extract_and_save(
        self,
        vendors: Optional[List[str]] = None,
        **kwargs
    ) -> List[str]:
        """
        Extrai props por game_id, buscando game_ids do storage e salvando imediatamente.
        
        Args:
            vendors: Lista de vendors (opcional, usa market configurado se não fornecido)
            **kwargs: Outros parâmetros (player_id, prop_type, etc.)
        
        Returns:
            Lista de caminhos completos dos arquivos salvos no GCS
        """
        logger.info(f"Iniciando extração do endpoint: {self.endpoint_name}")
        
        # Busca game_ids do storage
        logger.info(f"Buscando game_ids dos arquivos de games salvos no GCS...")
        game_ids = self.storage.get_game_ids_from_storage(self.season)
        
        if not game_ids:
            logger.warning("Nenhum game_id encontrado no storage. Execute extract_games.py primeiro.")
            return []
        
        logger.info(f"Encontrados {len(game_ids)} game_ids para processar")
        
        # Se vendors não fornecido, usa apenas o market configurado
        if not vendors:
            vendors = [self.market]
        
        saved_paths = []
        skipped_games = []
        
        # Processa cada game_id e cada vendor
        for game_id in game_ids:
            for vendor in vendors:
                try:
                    logger.info(f"=" * 60)
                    logger.info(f"Processando game_id {game_id} - vendor {vendor}...")
                    
                    # Extrai props para este game_id e vendor
                    data = self.extract(
                        game_id=game_id,
                        vendors=[vendor],
                        **{k: v for k, v in kwargs.items() if k != "vendors"}
                    )
                    props = data.get("props", [])
                    
                    if not props:
                        logger.info(f"Nenhuma prop encontrada para game_id {game_id} - vendor {vendor}. Pulando...")
                        skipped_games.append((game_id, vendor))
                        continue
                    
                    logger.info(f"Coletadas {len(props)} props para game_id {game_id} - vendor {vendor}")
                    
                    # Salva imediatamente
                    game_data = {
                        "season": self.season,
                        "game_id": game_id,
                        "vendor": vendor,
                        "total_props": len(props),
                        "props": props,
                    }
                    
                    gcs_path = self.save_to_gcs(game_data, market=vendor, game_id=game_id)
                    saved_paths.append(gcs_path)
                    logger.info(f"✓ Arquivo salvo: {gcs_path}")
                    
                except Exception as e:
                    logger.error(f"Erro ao processar game_id {game_id} - vendor {vendor}: {str(e)}", exc_info=True)
                    continue
        
        logger.info(f"=" * 60)
        logger.info(f"Extração concluída: {len(saved_paths)} arquivo(s) salvo(s)")
        if skipped_games:
            logger.info(f"Game/vendor sem dados: {len(skipped_games)}")
        
        return saved_paths
    
    def save_to_gcs(
        self,
        data: Dict[str, Any],
        market: Optional[str] = None,
        game_id: Optional[int] = None,
    ) -> str:
        """
        Salva dados no GCS com estrutura especial para player_props.
        
        Args:
            data: Dados a serem salvos
            market: Market/vendor (obrigatório para player_props)
            game_id: Game ID (obrigatório para player_props)
        
        Returns:
            Caminho completo do arquivo no GCS
        """
        if not market or not game_id:
            raise ValueError("market e game_id são obrigatórios para player_props")
        
        return self.storage.upload_json(
            data=data,
            endpoint=self.endpoint_name,
            season=self.season,
            market=market,
            game_id=game_id,
        )
