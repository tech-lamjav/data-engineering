"""Extractor para betting odds (spread, moneyline, total) por jogo."""
from typing import Dict, Any, List, Optional
from src.extractors.base_extractor import BaseExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class BettingOddsExtractor(BaseExtractor):
    """Extractor para betting odds de todos os vendors por jogo."""

    def __init__(self, season: int = None):
        """Inicializa o extractor de betting odds."""
        super().__init__("betting_odds", season)

    def extract(self, game_id: int) -> Dict[str, Any]:
        """
        Extrai odds de apostas para um jogo específico (todos os vendors).

        Args:
            game_id: ID do jogo (obrigatório)

        Returns:
            Dicionário com os dados extraídos
        """
        logger.info(f"Extraindo betting odds para game_id {game_id}...")

        odds = self.client.get_betting_odds(game_id=game_id)

        return {
            "season": self.season,
            "game_id": game_id,
            "total_odds": len(odds),
            "odds": odds,
        }

    def extract_and_save(self, **kwargs) -> List[str]:
        """
        Extrai odds por game_id, buscando game_ids do storage e salvando imediatamente.

        Returns:
            Lista de caminhos completos dos arquivos salvos no GCS
        """
        logger.info(f"Iniciando extração do endpoint: {self.endpoint_name}")

        logger.info("Buscando game_ids dos arquivos de games salvos no GCS...")
        game_ids = self.storage.get_game_ids_from_storage(self.season)

        if not game_ids:
            logger.warning("Nenhum game_id encontrado no storage. Execute extract_games.py primeiro.")
            return []

        logger.info(f"Encontrados {len(game_ids)} game_ids para processar")

        saved_paths = []
        skipped_games = []

        for game_id in game_ids:
            try:
                logger.info("=" * 60)
                logger.info(f"Processando game_id {game_id}...")

                data = self.extract(game_id=game_id)
                odds = data.get("odds", [])

                if not odds:
                    logger.warning(f"Nenhuma odd encontrada para game_id {game_id}. Pulando...")
                    skipped_games.append(game_id)
                    continue

                logger.info(f"Coletadas {len(odds)} odds para game_id {game_id}")

                gcs_path = self.save_to_gcs(data, game_id=game_id)
                saved_paths.append(gcs_path)
                logger.info(f"Arquivo salvo: {gcs_path}")

            except Exception as e:
                logger.error(f"Erro ao processar game_id {game_id}: {str(e)}", exc_info=True)
                continue

        logger.info("=" * 60)
        logger.info(f"Extração concluída: {len(saved_paths)} arquivo(s) salvo(s)")
        if skipped_games:
            logger.info(f"Games sem dados: {len(skipped_games)}")
        if not saved_paths:
            logger.warning(
                f"Nenhum arquivo salvo para endpoint: {self.endpoint_name}. "
                "Verifique se há odds disponíveis para os jogos da temporada."
            )

        return saved_paths

    def save_to_gcs(
        self,
        data: Dict[str, Any],
        game_id: Optional[int] = None,
    ) -> str:
        """
        Salva dados no GCS com estrutura por game_id.

        Args:
            data: Dados a serem salvos
            game_id: Game ID (obrigatório)

        Returns:
            Caminho completo do arquivo no GCS
        """
        if not game_id:
            raise ValueError("game_id é obrigatório para betting_odds")

        return self.storage.upload_json(
            data=data,
            endpoint=self.endpoint_name,
            season=self.season,
            game_id=game_id,
        )
