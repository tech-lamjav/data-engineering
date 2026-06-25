"""Extractor para /players da API-Football v3 (catálogo de jogadores)."""
from typing import Dict, Any
from src.utils.helpers import utcnow_iso
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import PLAYERS_BACKFILL, PLAYERS_CURRENT
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class PlayersExtractor(BaseExtractor):
    """Extrai catálogo de jogadores via /players?league=&season= (paginado).

    Modo "current" (default): ano corrente — usado pelo schedule diário.
    Modo "backfill": anos anteriores — one-shot manual.

    1 linha por (player, league, season) requisitada. O mesmo jogador pode
    aparecer em múltiplas (liga, temporada) — dedup acontece em dim_players.
    """

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="players",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in ("current", "backfill"):
            raise ValueError(f"mode inválido: {mode}. Use 'current' ou 'backfill'.")
        self.mode = mode
        self.targets = PLAYERS_CURRENT if mode == "current" else PLAYERS_BACKFILL

    def extract(self, **kwargs) -> Dict[str, Any]:
        """Itera sobre os targets e mescla resposta + metadata da request.

        `failed_targets` separa 'liga falhou' (errors no envelope) de 'liga sem dados'
        — extract_and_save usa isso p/ NÃO sobrescrever o arquivo bom com coleta parcial.
        """
        players = []
        failed_targets = []
        for league_id, season in self.targets:
            logger.info(f"Extraindo league={league_id} season={season}...")
            envelope = self.client.get_players(league_id, season)

            errors = envelope.get("errors")
            if errors:
                logger.error(
                    f"API errors para league={league_id} season={season}: {errors}"
                )
                failed_targets.append((league_id, season))
                continue

            response = envelope.get("response", []) or []
            if not response:
                logger.warning(
                    f"Resposta vazia para league={league_id} season={season}"
                )
                continue

            for item in response:
                players.append({
                    "requested_league_id": league_id,
                    "requested_season": season,
                    "loaded_at": utcnow_iso(),
                    **item,  # player: {...}, statistics: [...]
                })

        logger.info(
            f"Coletadas {len(players)} linhas (mode={self.mode}, targets={len(self.targets)})"
        )
        return {
            "mode": self.mode,
            "total_players": len(players),
            "failed_targets": failed_targets,
            "players": players,
        }

    def extract_and_save(self, **kwargs) -> str:
        """Override: usa mode no path do GCS em vez de date.

        Latest-only (fonte de verdade p/ dim_players): se algum target obrigatório
        FALHOU (errors no envelope — inclui estouro de quota a partir da página 2 que o
        cliente propaga em errors), NÃO sobrescreve o arquivo bom por uma coleta PARCIAL
        de jogadores — aborta com raise. 'Liga sem dados' (response vazio) é legítimo.
        """
        logger.info(f"Iniciando extração players (mode={self.mode})")
        data = self.extract(**kwargs)

        failed_targets = data.get("failed_targets") or []
        if failed_targets:
            raise RuntimeError(
                f"players (mode={self.mode}): {len(failed_targets)} target(s) falharam "
                f"({failed_targets}). Abortando p/ NÃO sobrescrever o catálogo bom no GCS "
                "com coleta parcial. Re-executar."
            )

        if data.get("total_players", 0) == 0:
            logger.warning("Nenhum jogador coletado — arquivo será uploadado vazio (metadata only)")

        data.pop("failed_targets", None)  # controle interno, fora do payload
        gcs_path = self.storage.upload_json(
            data=data,
            endpoint="players",
            season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
            sport="futebol",
            mode=self.mode,
        )
        logger.info(f"Extração concluída: {gcs_path}")
        return gcs_path
