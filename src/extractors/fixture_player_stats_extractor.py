"""Extractor para /fixtures/players da API-Football v3 (stats por jogador/jogo)."""
from typing import Dict, Any

from src.extractors.per_fixture_extractor import PerFixtureExtractor
from src.config import FIXTURE_PLAYER_STATS_REFETCH_WINDOW_DAYS
from src.utils.logger import setup_logger
from src.utils.helpers import utcnow_iso

logger = setup_logger(__name__)


class FixturePlayerStatsExtractor(PerFixtureExtractor):
    """Extrai estatística por jogador via /fixtures/players?fixture={id}.

    Endpoint é 1 chamada por fixture e só retorna dados após o jogo finalizar
    (status FT/AET/PEN). A lista de fixtures finalizados vem do arquivo de fixtures
    já salvo no GCS (raw_futebol_fixtures_{mode}.json) — mesma mecânica de
    statistics/events/lineups (skip-if-exists + janela 3d no current).

    Salva 1 arquivo por fixture com N linhas NDJSON (1 por jogador que entrou em
    campo — titulares + entrantes, ambos os times; ~22-30/jogo). Alimenta
    fact_fixture_player_stats e, via dim_players, garante que todo jogador que
    jogou tenha entrada na dimensão.

    Escopo:
    - backfill: busca todos os fixtures finalizados ainda sem arquivo (skip-if-exists).
    - current (diário): além do skip-if-exists, re-busca os jogos cujo kickoff foi
      nos últimos FIXTURE_PLAYER_STATS_REFETCH_WINDOW_DAYS dias (captura correções
      pós-jogo da API, ex.: rating revisado).
    """

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="fixture_player_stats",
            refetch_window_days=FIXTURE_PLAYER_STATS_REFETCH_WINDOW_DAYS,
            count_key="total_players",
            mode=mode,
        )

    def extract(self, fixture_id: int, **kwargs) -> Dict[str, Any]:
        """Bate em /fixtures/players e achata para 1 linha por jogador.

        A API aninha players[] em cada bloco de time e mete as stats do jogador
        num array de tamanho 1 (statistics[0]). Achatamos aqui para 1 linha por
        jogador {team, player, statistics(struct)}. Os valores aninhados ficam
        intactos (rating/accuracy vêm string; muitos campos null) — a tipagem fica
        no schema explícito da external table + casts no dbt (sem stringificar como
        no /fixtures/statistics, cujo array é {type, value} de tipo misto).
        """
        envelope = self.client.get_fixture_player_stats(fixture_id)

        errors = envelope.get("errors")
        if errors:
            logger.error(f"API errors para fixture={fixture_id}: {errors}")
            return {"total_players": 0, "fixture_player_stats": []}

        response = envelope.get("response", []) or []
        loaded_at = utcnow_iso()

        rows = []
        for team_block in response:
            team = team_block.get("team")
            for p in (team_block.get("players") or []):
                stats_list = p.get("statistics") or []
                rows.append({
                    "fixture_id": fixture_id,
                    "loaded_at": loaded_at,
                    "team": team,
                    "player": p.get("player"),
                    "statistics": stats_list[0] if stats_list else None,
                })

        return {"total_players": len(rows), "fixture_player_stats": rows}
