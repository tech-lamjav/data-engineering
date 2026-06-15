"""Extractor para /injuries da API-Football v3 (lesionados/suspensos)."""
import time
from datetime import datetime, timezone
from typing import Dict, Any
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import INJURIES_BACKFILL, INJURIES_CURRENT
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class InjuriesExtractor(BaseExtractor):
    """Extrai lesionados/suspensos via /injuries?league=&season= (1 chamada/alvo, paginada).

    Snapshot DIÁRIO de quem está fora (lesão) ou suspenso — input de modelagem que a
    maioria dos modelos públicos ignora (tirar um atacante de 0,7 gol/jogo muda a
    previsão). Igual ao standings, o arquivo é date-stampado:
    raw_futebol_injuries_{mode}_{YYYY-MM-DD}.json. O GCS acumula 1 arquivo por dia
    (histórico) e re-rodar no mesmo dia sobrescreve o mesmo arquivo — idempotente.

    Cada item do `response` é {player, team, fixture, league, type, reason} — achatamos
    1 linha por (player, fixture), carimbando requested_league_id/season + snapshot_date
    + loaded_at (como standings/team_season_stats). type ∈ {"Missing Fixture",
    "Questionable"}; reason é texto livre (inclui suspensões). snapshot_date = data da
    coleta (UTC).

    ⚠️ Coverage: só Brasileirão (71) tem coverage.injuries=TRUE — Copa do Mundo (1)
    fica fora dos targets (validado em dim_leagues). Mode "current" (default): ano
    corrente (Brasileirão 2026) — schedule diário. Mode "backfill": 2024/2025 — one-shot.
    """

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="injuries",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in ("current", "backfill"):
            raise ValueError(f"mode inválido: {mode}. Use 'current' ou 'backfill'.")
        self.mode = mode
        self.targets = INJURIES_CURRENT if mode == "current" else INJURIES_BACKFILL
        self.snapshot_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def extract(self, **kwargs) -> Dict[str, Any]:
        """Itera (liga, season) do mode e achata os lesionados em 1 linha por jogador×fixture."""
        rows = []
        empty = 0
        failed = 0

        for league_id, season in self.targets:
            try:
                envelope = self.client.get_injuries(league_id, season)
                time.sleep(0.4)  # cortesia entre chamadas (rate-limit API-Football)

                errors = envelope.get("errors")
                if errors:
                    logger.error(
                        f"API errors p/ league={league_id} season={season}: {errors}"
                    )
                    failed += 1
                    continue

                resp = envelope.get("response") or []
                if not resp:
                    # Liga sem lesões registradas (ex.: coverage off / início de temporada).
                    logger.info(
                        f"Sem injuries p/ league={league_id} season={season} (pulando)."
                    )
                    empty += 1
                    continue

                count_before = len(rows)
                for item in resp:
                    rows.append({
                        "requested_league_id": league_id,
                        "requested_season": season,
                        "snapshot_date": self.snapshot_date,
                        "loaded_at": datetime.utcnow().isoformat(),
                        **item,
                    })
                logger.info(
                    f"league={league_id} season={season}: {len(rows) - count_before} linhas."
                )
            except Exception as e:
                logger.error(
                    f"Erro p/ league={league_id} season={season}: {str(e)}",
                    exc_info=True,
                )
                failed += 1
                continue

        logger.info(
            f"Coletadas {len(rows)} linhas (mode={self.mode}, alvos={len(self.targets)}, "
            f"{empty} sem injuries, {failed} com erro)."
        )
        return {"total_rows": len(rows), "injuries": rows}

    def extract_and_save(self, **kwargs) -> str:
        """Override: arquivo date-stampado por mode (1 snapshot por dia no GCS)."""
        logger.info(
            f"Iniciando extração injuries (mode={self.mode}, snapshot_date={self.snapshot_date})"
        )
        data = self.extract(**kwargs)

        if data.get("total_rows", 0) == 0:
            # Sem upload: um arquivo metadata-only viraria linha NULL na external table.
            logger.warning("Nenhuma linha coletada — upload pulado (sem arquivo vazio).")
            return ""

        gcs_path = self.storage.upload_json(
            data=data,
            endpoint="injuries",
            season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
            sport="futebol",
            mode=self.mode,
            date=self.snapshot_date,
        )
        logger.info(f"Extração concluída: {gcs_path}")
        return gcs_path
