"""Extractor para /fixtures/statistics da API-Football v3 (stats por time/jogo)."""
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import FIXTURE_STATS_REFETCH_WINDOW_DAYS, get_gcs_path
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class FixtureStatisticsExtractor(BaseExtractor):
    """Extrai estatística agregada por time via /fixtures/statistics?fixture={id}.

    Endpoint é 1 chamada por fixture e só retorna dados após o jogo finalizar
    (status FT/AET/PEN). A lista de fixtures finalizados vem do arquivo de fixtures
    já salvo no GCS (raw_futebol_fixtures_{mode}.json) — espelha o padrão per-game
    NBA (BettingOddsExtractor) combinado com o split de mode do futebol.

    Salva 1 arquivo por fixture (2 linhas NDJSON: mandante e visitante).

    Escopo:
    - backfill: busca todos os fixtures finalizados ainda sem arquivo (skip-if-exists).
    - current (diário): além do skip-if-exists, re-busca os jogos cujo kickoff foi
      nos últimos FIXTURE_STATS_REFETCH_WINDOW_DAYS dias (captura correções pós-jogo).
    """

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="fixture_statistics",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in ("current", "backfill"):
            raise ValueError(f"mode inválido: {mode}. Use 'current' ou 'backfill'.")
        self.mode = mode
        self.window_days = FIXTURE_STATS_REFETCH_WINDOW_DAYS

    def extract(self, fixture_id: int, **kwargs) -> Dict[str, Any]:
        """Bate em /fixtures/statistics para um fixture e monta os blocos por time.

        Stringifica todos os `value` (a API mistura int, "55%" e null no mesmo
        array — autodetect do BigQuery quebraria). O parse numérico fica no dbt.
        """
        envelope = self.client.get_fixture_statistics(fixture_id)

        errors = envelope.get("errors")
        if errors:
            logger.error(f"API errors para fixture={fixture_id}: {errors}")
            return {"total_teams": 0, "fixture_statistics": []}

        response = envelope.get("response", []) or []
        loaded_at = datetime.utcnow().isoformat()

        blocks = []
        for item in response:
            stats = []
            for s in (item.get("statistics") or []):
                value = s.get("value")
                stats.append({
                    "type": s.get("type"),
                    "value": str(value) if value is not None else None,
                })
            blocks.append({
                "fixture_id": fixture_id,
                "loaded_at": loaded_at,
                "team": item.get("team"),
                "statistics": stats,
            })

        return {"total_teams": len(blocks), "fixture_statistics": blocks}

    def extract_and_save(self, **kwargs) -> List[str]:
        """Itera os fixtures finalizados, aplica janela + skip-if-exists, salva 1 arquivo/jogo."""
        logger.info(f"Iniciando extração fixture_statistics (mode={self.mode})")

        fixtures = self.storage.get_fixture_ids_from_storage(self.mode)
        if not fixtures:
            logger.warning(
                "Nenhum fixture finalizado encontrado. Rode extract_fixtures antes."
            )
            return []

        cutoff = datetime.now(timezone.utc).date() - timedelta(days=self.window_days)
        saved_paths = []
        skipped = 0
        empty = 0
        failed = 0

        for fx in fixtures:
            fixture_id = fx["fixture_id"]
            try:
                fixture_date = datetime.strptime(fx["date_utc"], "%Y-%m-%d").date()
            except (ValueError, TypeError, KeyError):
                fixture_date = None

            blob_path = get_gcs_path(
                "fixture_statistics", 0, sport="futebol", game_id=fixture_id
            )

            # current: re-busca jogos recentes (correções); backfill: só o que falta.
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

                if data.get("total_teams", 0) == 0:
                    # Jogo pode estar FT mas sem stats populadas ainda — não grava,
                    # para ser re-tentado no próximo run (senão skip-if-exists o pula pra sempre).
                    logger.info(f"Fixture {fixture_id}: stats vazias, pulando (re-tenta depois).")
                    empty += 1
                    continue

                gcs_path = self.storage.upload_json(
                    data=data,
                    endpoint="fixture_statistics",
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
            f"fixture_statistics concluído (mode={self.mode}): {len(saved_paths)} salvos, "
            f"{skipped} pulados (já existem), {empty} vazios, {failed} com erro (re-tentar)."
        )
        return saved_paths
