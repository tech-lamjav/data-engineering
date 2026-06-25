"""Extractor para /fixtures/lineups da API-Football v3 (escalações por time/jogo)."""
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import (
    FIXTURE_LINEUPS_REFETCH_WINDOW_DAYS,
    FIXTURE_LINEUPS_PREGAME_WINDOW_MIN,
    get_gcs_path,
)
from src.utils.logger import setup_logger
from src.utils.helpers import utcnow_iso

logger = setup_logger(__name__)


class FixtureLineupsExtractor(BaseExtractor):
    """Extrai escalações por time via /fixtures/lineups?fixture={id}.

    1 chamada por fixture, 1 arquivo por (fixture, fase). Duas fases:
    - "real" (mode current/backfill): escalação pós-jogo (fonte da verdade). A lista de
      fixtures finalizados (FT/AET/PEN) vem de get_fixture_ids_from_storage — mesma
      mecânica de statistics/events (skip-if-exists + janela 3d no current).
    - "confirmed" (mode pregame): escalação confirmada ~T-30min. A lista vem de
      get_upcoming_fixture_ids (jogos NS com kickoff iminente); skip-if-exists puro.

    O fato é latest-wins (dedup por loaded_at no dbt) — "real" vence "confirmed". O GCS
    guarda os dois snapshots (arquivos _confirmed e _real), perda-zero/reversível.

    Salva 1 arquivo por (fixture, fase) com 2 linhas NDJSON (mandante e visitante);
    startXI/substitutes ficam aninhados (UNNEST acontece no dbt).
    """

    VALID_MODES = ("current", "backfill", "pregame")

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="fixture_lineups",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in self.VALID_MODES:
            raise ValueError(
                f"mode inválido: {mode}. Use 'current', 'backfill' ou 'pregame'."
            )
        self.mode = mode
        self.window_days = FIXTURE_LINEUPS_REFETCH_WINDOW_DAYS
        # pregame grava lineup_phase="confirmed"; current/backfill gravam "real".
        self.phase = "confirmed" if mode == "pregame" else "real"

    def extract(self, fixture_id: int, **kwargs) -> Dict[str, Any]:
        """Bate em /fixtures/lineups para um fixture e monta os blocos por time.

        Mantém startXI/substitutes aninhados (o flatten/UNNEST acontece no dbt) e
        carimba lineup_phase (confirmed|real) em cada bloco.
        """
        envelope = self.client.get_fixture_lineups(fixture_id)

        errors = envelope.get("errors")
        if errors:
            logger.error(f"API errors para fixture={fixture_id}: {errors}")
            return {"total_teams": 0, "fixture_lineups": []}

        response = envelope.get("response", []) or []
        loaded_at = utcnow_iso()

        blocks = []
        for item in response:
            blocks.append({
                "fixture_id": fixture_id,
                "loaded_at": loaded_at,
                "lineup_phase": self.phase,
                "team": item.get("team"),
                "coach": item.get("coach"),
                "formation": item.get("formation"),
                "startXI": item.get("startXI") or [],
                "substitutes": item.get("substitutes") or [],
            })

        return {"total_teams": len(blocks), "fixture_lineups": blocks}

    def extract_and_save(self, **kwargs) -> List[str]:
        """Itera os fixtures-alvo (pós-jogo ou pré-jogo), aplica skip-if-exists, salva 1 arquivo/jogo/fase."""
        logger.info(
            f"Iniciando extração fixture_lineups (mode={self.mode}, phase={self.phase})"
        )

        if self.mode == "pregame":
            fixtures = self.storage.get_upcoming_fixture_ids(
                FIXTURE_LINEUPS_PREGAME_WINDOW_MIN
            )
            if not fixtures:
                logger.info("Nenhum jogo NS com kickoff iminente — nada a coletar.")
                return []
        else:
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
                "fixture_lineups", 0, sport="futebol",
                game_id=fixture_id, mode=self.phase,
            )

            # current: re-busca jogos recentes (correções pós-jogo da API). pregame e
            # backfill: skip-if-exists puro (captura a escalação uma vez e segue).
            is_recent = (
                self.mode == "current"
                and fixture_date is not None
                and fixture_date >= cutoff
            )

            # Erros transitórios (timeout de API ou de GCS) não devem abortar o run
            # inteiro — loga, conta como falha e segue; skip-if-exists re-tenta no próximo run.
            try:
                if self.storage.bucket.blob(blob_path).exists() and not is_recent:
                    skipped += 1
                    continue

                data = self.extract(fixture_id=fixture_id)
                time.sleep(0.4)  # cortesia entre chamadas (rate-limit API-Football)

                if data.get("total_teams", 0) == 0:
                    # Escalação ainda não publicada (esperado no 1º poll pré-jogo, ~T-45) ou
                    # jogo sem lineup: NÃO grava, p/ ser re-tentado no próximo run. Gravar
                    # vazio aqui faria o skip-if-exists bloquear os polls seguintes (T-30/T-15)
                    # e a escalação nunca seria capturada.
                    logger.info(
                        f"Fixture {fixture_id}: lineups vazias, pulando (re-tenta depois)."
                    )
                    empty += 1
                    continue

                gcs_path = self.storage.upload_json(
                    data=data,
                    endpoint="fixture_lineups",
                    season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
                    sport="futebol",
                    game_id=fixture_id,
                    mode=self.phase,  # sufixo _confirmed | _real no nome do arquivo
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
            f"fixture_lineups concluído (mode={self.mode}, phase={self.phase}): "
            f"{len(saved_paths)} salvos, {skipped} pulados (já existem), "
            f"{empty} vazios, {failed} com erro (re-tentar)."
        )
        return saved_paths
