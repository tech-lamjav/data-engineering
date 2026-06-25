"""Extractor para /leagues da API-Football v3."""
from typing import Dict, Any
from src.utils.helpers import utcnow_iso
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import LEAGUES_BACKFILL, LEAGUES_CURRENT
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class LeaguesExtractor(BaseExtractor):
    """Extrai metadados de ligas/temporadas via /leagues.

    Modo "current" (default): ano corrente — usado pelo schedule diário.
    Modo "backfill": anos anteriores — one-shot manual.
    """

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="leagues",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in ("current", "backfill"):
            raise ValueError(f"mode inválido: {mode}. Use 'current' ou 'backfill'.")
        self.mode = mode
        self.targets = LEAGUES_CURRENT if mode == "current" else LEAGUES_BACKFILL

    def extract(self, **kwargs) -> Dict[str, Any]:
        """Itera sobre os targets e mescla resposta + metadata da request."""
        leagues = []
        for league_id, season in self.targets:
            logger.info(f"Extraindo league={league_id} season={season}...")
            envelope = self.client.get_league(league_id, season)

            errors = envelope.get("errors")
            # A API retorna errors como list (vazia) ou dict; ambos truthy quando há erro.
            if errors:
                logger.error(
                    f"API errors para league={league_id} season={season}: {errors}"
                )
                continue

            response = envelope.get("response", []) or []
            if not response:
                logger.warning(
                    f"Resposta vazia para league={league_id} season={season}"
                )
                continue

            for item in response:
                leagues.append({
                    "requested_league_id": league_id,
                    "requested_season": season,
                    "loaded_at": utcnow_iso(),
                    **item,  # league: {...}, country: {...}, seasons: [...]
                })

        logger.info(
            f"Coletadas {len(leagues)} linhas (mode={self.mode}, targets={len(self.targets)})"
        )
        return {
            "mode": self.mode,
            "total_leagues": len(leagues),
            "leagues": leagues,
        }

    def extract_and_save(self, **kwargs) -> str:
        """Override: usa mode no path do GCS em vez de date."""
        logger.info(f"Iniciando extração leagues (mode={self.mode})")
        data = self.extract(**kwargs)

        if data.get("total_leagues", 0) == 0:
            logger.warning("Nenhuma liga coletada — arquivo será uploadado vazio (metadata only)")

        gcs_path = self.storage.upload_json(
            data=data,
            endpoint="leagues",
            season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
            sport="futebol",
            mode=self.mode,
        )
        logger.info(f"Extração concluída: {gcs_path}")
        return gcs_path
