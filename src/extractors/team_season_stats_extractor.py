"""Extractor para /teams/statistics da API-Football v3 (agregados de temporada)."""
import time
from datetime import datetime, timezone
from typing import Dict, Any, List
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.utils.logger import setup_logger
from src.utils.helpers import utcnow_iso

logger = setup_logger(__name__)


class TeamSeasonStatsExtractor(BaseExtractor):
    """Extrai agregados de temporada por time via /teams/statistics?league=&season=&team=.

    1 chamada por (time, liga, temporada) — input direto pro modelo Poisson (força
    ataque/defesa, médias casa/fora). A lista de times vem do arquivo de teams já salvo
    no GCS (raw_futebol_teams_{mode}.json) — mesma mecânica do player_stats lendo fixtures.

    Latest-only (igual aos demais fatos de futebol): salva 1 arquivo por mode com N linhas
    NDJSON (1 por time). snapshot_date carimba a data da coleta (UTC) — é a chave de
    partição em fact_team_season_stats, mesmo o run sobrescrevendo o arquivo a cada semana.

    Modo "current" (default): ano corrente (Brasileirão 2026 + Copa 2026) — schedule semanal.
    Modo "backfill": anos anteriores (Brasileirão 2024/2025) — one-shot manual.

    ⚠️ O `response` do /teams/statistics é um OBJETO ÚNICO (não lista). Achatamos um
    subconjunto curado pro Poisson; os buckets por minuto (goals.*.minute, chaves "0-15"
    com hífen), under_over, cards e lineups[] são DESCARTADOS (redundantes com
    fact_fixture_events/fact_fixture_lineups e/ou problemáticos como nome de coluna).
    """

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="team_season_stats",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in ("current", "backfill"):
            raise ValueError(f"mode inválido: {mode}. Use 'current' ou 'backfill'.")
        self.mode = mode

    def _curate(self, resp: Dict[str, Any]) -> Dict[str, Any]:
        """Seleciona do objeto /teams/statistics só o subconjunto curado pro Poisson.

        Mantém a forma aninhada da API (achatada em stg) e descarta os buckets por
        minuto / under_over / cards / lineups.
        """
        goals = resp.get("goals") or {}
        goals_for = goals.get("for") or {}
        goals_against = goals.get("against") or {}
        biggest = resp.get("biggest") or {}
        penalty = resp.get("penalty") or {}
        return {
            "team": resp.get("team"),
            "form": resp.get("form"),
            "fixtures": resp.get("fixtures"),  # played/wins/draws/loses × {home,away,total}
            "goals": {
                "for": {
                    "total": goals_for.get("total"),       # {home,away,total} INT
                    "average": goals_for.get("average"),   # {home,away,total} STRING
                },
                "against": {
                    "total": goals_against.get("total"),
                    "average": goals_against.get("average"),
                },
            },
            "clean_sheet": resp.get("clean_sheet"),         # {home,away,total}
            "failed_to_score": resp.get("failed_to_score"), # {home,away,total}
            "biggest": {
                "streak": biggest.get("streak"),            # {wins,draws,loses}
                "wins": biggest.get("wins"),                # {home,away} STRINGs "3-0"
                "loses": biggest.get("loses"),              # {home,away} STRINGs
                "goals": biggest.get("goals"),              # {for:{home,away}, against:{home,away}}
            },
            "penalty": {
                "scored": penalty.get("scored"),            # {total, percentage STRING}
                "missed": penalty.get("missed"),
                "total": penalty.get("total"),
            },
        }

    def extract(self, **kwargs) -> Dict[str, Any]:
        """Itera os times do mode e achata 1 linha curada por (time, liga, season)."""
        teams = self.storage.get_team_ids_from_storage(self.mode)
        if not teams:
            logger.warning(
                "Nenhum time encontrado no GCS. Rode extract_teams antes (mode igual)."
            )
            return {"total_teams": 0, "team_season_stats": []}

        snapshot_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = []
        empty = 0
        failed = 0

        for t in teams:
            league_id, season, team_id = t["league_id"], t["season"], t["team_id"]
            try:
                envelope = self.client.get_team_season_stats(league_id, season, team_id)
                time.sleep(0.4)  # cortesia entre chamadas (rate-limit API-Football)

                errors = envelope.get("errors")
                if errors:
                    logger.error(
                        f"API errors p/ league={league_id} season={season} team={team_id}: {errors}"
                    )
                    failed += 1
                    continue

                resp = envelope.get("response") or {}
                if not resp or not resp.get("team"):
                    logger.info(
                        f"Sem stats p/ league={league_id} season={season} team={team_id} (pulando)."
                    )
                    empty += 1
                    continue

                rows.append({
                    "requested_league_id": league_id,
                    "requested_season": season,
                    "snapshot_date": snapshot_date,
                    "loaded_at": utcnow_iso(),
                    **self._curate(resp),
                })
            except Exception as e:
                logger.error(
                    f"Erro p/ league={league_id} season={season} team={team_id}: {str(e)}",
                    exc_info=True,
                )
                failed += 1
                continue

        logger.info(
            f"Coletadas {len(rows)} linhas (mode={self.mode}, times={len(teams)}, "
            f"{empty} sem stats, {failed} com erro)."
        )
        return {
            "total_teams": len(rows),
            "failed": failed,
            "team_season_stats": rows,
        }

    def extract_and_save(self, **kwargs) -> str:
        """Override: usa mode no path do GCS (latest-only, 1 arquivo por mode).

        Latest-only (input direto do Poisson): se algum time FALHOU (errors no envelope
        ou exceção — distinto de 'time sem stats', que é legítimo), NÃO sobrescreve o
        arquivo bom anterior por uma coleta parcial — aborta com raise p/ preservar o
        snapshot. Re-executar capta os times que faltaram.
        """
        logger.info(f"Iniciando extração team_season_stats (mode={self.mode})")
        data = self.extract(**kwargs)

        failed = data.get("failed", 0)
        if failed:
            raise RuntimeError(
                f"team_season_stats (mode={self.mode}): {failed} time(s) falharam. "
                "Abortando p/ NÃO sobrescrever o arquivo bom no GCS com coleta parcial. Re-executar."
            )

        if data.get("total_teams", 0) == 0:
            logger.warning("Nenhuma stat coletada — arquivo será uploadado vazio (metadata only)")

        data.pop("failed", None)  # controle interno, fora do payload
        gcs_path = self.storage.upload_json(
            data=data,
            endpoint="team_season_stats",
            season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
            sport="futebol",
            mode=self.mode,
        )
        logger.info(f"Extração concluída: {gcs_path}")
        return gcs_path
