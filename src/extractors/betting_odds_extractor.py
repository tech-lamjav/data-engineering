"""Extractor para betting odds (spread, moneyline, total) por jogo."""
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from src.extractors.base_extractor import BaseExtractor
from src.config import (
    get_gcs_path,
    NBA_PER_GAME_REFETCH_WINDOW_DAYS,
    NBA_PER_GAME_SLEEP_SECONDS,
)
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

        Aplica skip-if-exists + janela de re-fetch (igual aos per-fixture de futebol):
        jogos cujo arquivo já existe no GCS são pulados, exceto os dos últimos
        NBA_PER_GAME_REFETCH_WINDOW_DAYS dias (odds ainda podem mudar). Jogos sem data
        conhecida são re-buscados por segurança. Distingue 'sem odds' (empty) de
        'falhou' (exceção) — emite ERROR de RESUMO quando houver falhas.

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

        game_dates = self.storage.get_game_dates_from_storage(self.season)
        cutoff = datetime.now(timezone.utc).date() - timedelta(
            days=NBA_PER_GAME_REFETCH_WINDOW_DAYS
        )

        saved_paths = []
        skipped_games = []
        skipped_exists = 0
        failed_games = []

        for game_id in game_ids:
            try:
                # Skip-if-exists + janela: pula jogos antigos já salvos; re-busca
                # jogos recentes (ou sem data conhecida) p/ capturar linhas atualizadas.
                game_date = self._parse_date(game_dates.get(game_id))
                is_recent = game_date is None or game_date >= cutoff
                blob_path = get_gcs_path(self.endpoint_name, self.season, game_id=game_id)
                if not is_recent and self.storage.bucket.blob(blob_path).exists():
                    skipped_exists += 1
                    continue

                logger.info("=" * 60)
                logger.info(f"Processando game_id {game_id}...")

                data = self.extract(game_id=game_id)
                time.sleep(NBA_PER_GAME_SLEEP_SECONDS)  # cortesia entre chamadas
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
                failed_games.append(game_id)
                continue

        logger.info("=" * 60)
        logger.info(
            f"Extração concluída: {len(saved_paths)} salvos, {skipped_exists} pulados "
            f"(já existem), {len(skipped_games)} sem odds, {len(failed_games)} com erro."
        )
        if failed_games:
            logger.error(
                f"RESUMO DE FALHA — {self.endpoint_name}: {len(failed_games)} game(s) "
                f"falharam ({failed_games[:5]}...). Dados podem estar incompletos — re-executar."
            )
        elif not saved_paths and skipped_exists == 0:
            logger.warning(
                f"Nenhum arquivo salvo para endpoint: {self.endpoint_name}. "
                "Verifique se há odds disponíveis para os jogos da temporada."
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
