"""Extractor para /fixtures/events da API-Football v3 (linha do tempo do jogo)."""
from typing import Dict, Any

from src.extractors.per_fixture_extractor import PerFixtureExtractor
from src.config import FIXTURE_EVENTS_REFETCH_WINDOW_DAYS
from src.utils.logger import setup_logger
from src.utils.helpers import utcnow_iso

logger = setup_logger(__name__)


class FixtureEventsExtractor(PerFixtureExtractor):
    """Extrai a linha do tempo do jogo via /fixtures/events?fixture={id}.

    Endpoint é 1 chamada por fixture e só retorna dados após o jogo finalizar
    (status FT/AET/PEN). A lista de fixtures finalizados vem do arquivo de fixtures
    já salvo no GCS (raw_futebol_fixtures_{mode}.json) — mesmo padrão da subtask 5
    (FixtureStatisticsExtractor).

    Salva 1 arquivo por fixture, com N linhas NDJSON (1 por evento). A API NÃO traz
    id nem campo de ordem nos eventos — a posição no array é a ordem cronológica,
    então carimbamos `event_order` (índice) em cada evento antes de salvar; sem isso
    o achatamento em linhas NDJSON perderia a ordem.

    Escopo:
    - backfill: busca todos os fixtures finalizados ainda sem arquivo (skip-if-exists).
    - current (diário): além do skip-if-exists, re-busca os jogos cujo kickoff foi
      nos últimos FIXTURE_EVENTS_REFETCH_WINDOW_DAYS dias (captura VAR/correções pós-jogo).
    """

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="fixture_events",
            refetch_window_days=FIXTURE_EVENTS_REFETCH_WINDOW_DAYS,
            count_key="total_events",
            mode=mode,
        )

    def extract(self, fixture_id: int, **kwargs) -> Dict[str, Any]:
        """Bate em /fixtures/events para um fixture e monta 1 registro por evento.

        Carimba event_order = índice do evento no array `response` (a API entrega
        em ordem cronológica e não traz chave de ordem própria). team/player/assist
        ficam aninhados (achatados depois em stg_futebol_fixture_events).
        """
        envelope = self.client.get_fixture_events(fixture_id)

        errors = envelope.get("errors")
        if errors:
            logger.error(f"API errors para fixture={fixture_id}: {errors}")
            return {"total_events": 0, "fixture_events": []}

        response = envelope.get("response", []) or []
        loaded_at = utcnow_iso()

        events = []
        for i, e in enumerate(response):
            t = e.get("time") or {}
            events.append({
                "fixture_id": fixture_id,
                "loaded_at": loaded_at,
                "event_order": i,  # índice = ordem cronológica (preserva a ordem)
                "elapsed": t.get("elapsed"),
                "extra": t.get("extra"),
                "team": e.get("team"),
                "player": e.get("player"),
                "assist": e.get("assist"),
                "type": e.get("type"),
                "detail": e.get("detail"),
                "comments": e.get("comments"),
            })

        return {"total_events": len(events), "fixture_events": events}
