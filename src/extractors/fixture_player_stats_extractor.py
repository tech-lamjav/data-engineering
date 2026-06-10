"""Extractor para /fixtures/players da API-Football v3 (stats por jogador/jogo)."""
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import FIXTURE_PLAYER_STATS_REFETCH_WINDOW_DAYS, get_gcs_path
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class FixturePlayerStatsExtractor(BaseExtractor):
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
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in ("current", "backfill"):
            raise ValueError(f"mode inválido: {mode}. Use 'current' ou 'backfill'.")
        self.mode = mode
        self.window_days = FIXTURE_PLAYER_STATS_REFETCH_WINDOW_DAYS

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
        loaded_at = datetime.utcnow().isoformat()

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

    def extract_and_save(self, **kwargs) -> List[str]:
        """Itera os fixtures finalizados, aplica janela + skip-if-exists, salva 1 arquivo/jogo."""
        logger.info(f"Iniciando extração fixture_player_stats (mode={self.mode})")

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
                "fixture_player_stats", 0, sport="futebol", game_id=fixture_id
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

                if data.get("total_players", 0) == 0:
                    # Jogo pode estar FT mas sem player stats populadas ainda — não grava,
                    # para ser re-tentado no próximo run (senão skip-if-exists o pula pra sempre).
                    logger.info(
                        f"Fixture {fixture_id}: player stats vazias, pulando (re-tenta depois)."
                    )
                    empty += 1
                    continue

                gcs_path = self.storage.upload_json(
                    data=data,
                    endpoint="fixture_player_stats",
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
            f"fixture_player_stats concluído (mode={self.mode}): {len(saved_paths)} salvos, "
            f"{skipped} pulados (já existem), {empty} vazios, {failed} com erro (re-tentar)."
        )
        return saved_paths
