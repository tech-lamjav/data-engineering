"""Extractor para /fixtures/statistics da API-Football v3 (stats por time/jogo)."""
from typing import Dict, Any

from src.extractors.per_fixture_extractor import PerFixtureExtractor
from src.config import FIXTURE_STATS_REFETCH_WINDOW_DAYS
from src.utils.logger import setup_logger
from src.utils.helpers import utcnow_iso

logger = setup_logger(__name__)


class FixtureStatisticsExtractor(PerFixtureExtractor):
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
            refetch_window_days=FIXTURE_STATS_REFETCH_WINDOW_DAYS,
            count_key="total_teams",
            mode=mode,
        )

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
        loaded_at = utcnow_iso()

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
