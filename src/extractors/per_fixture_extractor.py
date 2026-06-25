"""Base para extractors per-fixture da API-Football (statistics/events/player_stats).

Os três endpoints per-fixture só diferem no extract() (formato do registro) e no
endpoint/janela de re-fetch. O loop de extract_and_save — iterar os fixtures
finalizados (lidos do arquivo de fixtures no GCS), aplicar skip-if-exists + janela
de re-fetch no modo current, contabilizar salvos/pulados/vazios/falhas e salvar 1
arquivo por fixture — é idêntico e fica centralizado aqui.
"""
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import get_gcs_path
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class PerFixtureExtractor(BaseExtractor):
    """Extractor base para endpoints /fixtures/{statistics,events,players}.

    Cada chamada é 1 fixture e só retorna dados após o jogo finalizar (FT/AET/PEN).
    A lista de fixtures finalizados vem de get_fixture_ids_from_storage(mode).

    Subclasses definem (via super().__init__): endpoint_name, refetch_window_days,
    count_key (chave de contagem no dict retornado por extract()) e implementam
    extract(fixture_id) com o formato específico do endpoint.
    """

    def __init__(
        self,
        endpoint_name: str,
        refetch_window_days: int,
        count_key: str,
        mode: str = "current",
    ):
        super().__init__(
            endpoint_name=endpoint_name,
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in ("current", "backfill"):
            raise ValueError(f"mode inválido: {mode}. Use 'current' ou 'backfill'.")
        self.mode = mode
        self.window_days = refetch_window_days
        self.count_key = count_key

    def extract_and_save(self, **kwargs) -> List[str]:
        """Itera os fixtures finalizados, aplica janela + skip-if-exists, salva 1 arquivo/jogo."""
        logger.info(f"Iniciando extração {self.endpoint_name} (mode={self.mode})")

        fixtures = self.storage.get_fixture_ids_from_storage(self.mode)
        if not fixtures:
            logger.warning(
                "Nenhum fixture finalizado encontrado. Rode extract_fixtures antes."
            )
            return []

        cutoff = datetime.now(timezone.utc).date() - timedelta(days=self.window_days)
        saved_paths: List[str] = []
        skipped = empty = failed = 0

        for fx in fixtures:
            fixture_id = fx["fixture_id"]
            try:
                fixture_date = datetime.strptime(fx["date_utc"], "%Y-%m-%d").date()
            except (ValueError, TypeError, KeyError):
                fixture_date = None

            blob_path = get_gcs_path(
                self.endpoint_name, 0, sport="futebol", game_id=fixture_id
            )

            # current: re-busca jogos recentes (correções pós-jogo); backfill: só o que falta.
            is_recent = (
                self.mode == "current"
                and fixture_date is not None
                and fixture_date >= cutoff
            )

            # Erros transitórios (timeout de API ou de GCS) não devem abortar o backfill
            # inteiro — loga, conta como falha e segue; skip-if-exists re-tenta no próximo run.
            try:
                if self.storage.bucket.blob(blob_path).exists() and not is_recent:
                    skipped += 1
                    continue

                data = self.extract(fixture_id=fixture_id)
                time.sleep(0.4)  # cortesia entre chamadas (rate-limit API-Football)

                if data.get(self.count_key, 0) == 0:
                    # Jogo pode estar FT mas sem dados populados ainda — não grava, para
                    # ser re-tentado no próximo run (senão skip-if-exists o pula pra sempre).
                    logger.info(
                        f"Fixture {fixture_id}: {self.endpoint_name} vazio, pulando (re-tenta depois)."
                    )
                    empty += 1
                    continue

                gcs_path = self.storage.upload_json(
                    data=data,
                    endpoint=self.endpoint_name,
                    season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
                    sport="futebol",
                    game_id=fixture_id,
                )
                saved_paths.append(gcs_path)
            except Exception as e:
                logger.error(
                    f"Erro ao processar fixture {fixture_id}: {str(e)}",
                    exc_info=True,
                )
                failed += 1
                continue

        logger.info(
            f"{self.endpoint_name} concluído (mode={self.mode}): {len(saved_paths)} salvos, "
            f"{skipped} pulados (já existem), {empty} vazios, {failed} com erro (re-tentar)."
        )
        return saved_paths
