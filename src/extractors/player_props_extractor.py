"""Extractor para props de jogadores."""
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from src.extractors.base_extractor import BaseExtractor
from src.config import (
    get_gcs_path,
    NBA_PER_GAME_REFETCH_WINDOW_DAYS,
    NBA_PER_GAME_SLEEP_SECONDS,
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class PlayerPropsExtractor(BaseExtractor):
    """Extractor para props de jogadores."""
    
    def __init__(self, season: int = None):
        """Inicializa o extractor de props."""
        super().__init__("player_props", season)
        self.market = self.config.get("market", "draftkings")
        self.vendors = self.config.get("vendors", [self.market])
    
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
            vendors: Lista de vendors (opcional, usa todos os vendors configurados se não fornecido)
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

        # Se vendors não fornecido, usa todos os vendors configurados
        if not vendors:
            vendors = self.vendors

        # Skip-if-exists + janela de re-fetch (igual aos per-fixture de futebol):
        # combinações (game_id, vendor) já salvas de jogos antigos são puladas; jogos
        # recentes (ou sem data conhecida) são re-buscados (props ainda podem mudar).
        game_dates = self.storage.get_game_dates_from_storage(self.season)
        cutoff = datetime.now(timezone.utc).date() - timedelta(
            days=NBA_PER_GAME_REFETCH_WINDOW_DAYS
        )

        saved_paths = []
        skipped_games = []
        skipped_exists = 0
        failed_games = []

        # Processa cada game_id e cada vendor
        for game_id in game_ids:
            game_date = self._parse_date(game_dates.get(game_id))
            is_recent = game_date is None or game_date >= cutoff
            for vendor in vendors:
                try:
                    blob_path = get_gcs_path(
                        self.endpoint_name, self.season, market=vendor, game_id=game_id
                    )
                    if not is_recent and self.storage.bucket.blob(blob_path).exists():
                        skipped_exists += 1
                        continue

                    logger.info(f"=" * 60)
                    logger.info(f"Processando game_id {game_id} - vendor {vendor}...")

                    # Extrai props para este game_id e vendor
                    data = self.extract(
                        game_id=game_id,
                        vendors=[vendor],
                        **{k: v for k, v in kwargs.items() if k != "vendors"}
                    )
                    time.sleep(NBA_PER_GAME_SLEEP_SECONDS)  # cortesia entre chamadas
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
                    failed_games.append((game_id, vendor))
                    continue

        logger.info(f"=" * 60)
        logger.info(
            f"Extração concluída: {len(saved_paths)} salvos, {skipped_exists} pulados "
            f"(já existem), {len(skipped_games)} sem props, {len(failed_games)} com erro."
        )
        if failed_games:
            logger.error(
                f"RESUMO DE FALHA — {self.endpoint_name}: {len(failed_games)} combinação(ões) "
                f"game/vendor falharam ({failed_games[:5]}...). Dados podem estar incompletos — re-executar."
            )

        return saved_paths

    @staticmethod
    def _parse_date(date_str: Optional[str]):
        """Converte 'YYYY-MM-DD' em date; None se ausente/inválida (re-busca p/ segurança)."""
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    
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
