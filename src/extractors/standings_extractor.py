"""Extractor para /standings da API-Football v3 (tabela do campeonato)."""
import time
from datetime import datetime, timezone
from typing import Dict, Any
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import STANDINGS_BACKFILL, STANDINGS_CURRENT
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class StandingsExtractor(BaseExtractor):
    """Extrai a classificação da liga via /standings?league=&season= (1 chamada por alvo).

    Snapshot DIÁRIO da tabela — contexto de modelagem (briga pelo G4, rebaixamento,
    jogo já decidido) e histórico de evolução. Diferente dos demais fatos de futebol
    (latest-only), o arquivo é date-stampado: raw_futebol_standings_{mode}_{YYYY-MM-DD}.json.
    O GCS acumula 1 arquivo por dia (fonte de verdade do histórico) e re-rodar no mesmo
    dia sobrescreve o mesmo arquivo — idempotente por construção.

    `response[0].league.standings` é um ARRAY DE ARRAYS (1 array interno por grupo —
    Brasileirão tem 1, Copa do Mundo tem N). Achatamos os dois níveis em 1 linha por
    time, mantendo os campos da API como vêm (rank, team, points, goalsDiff, group,
    form, status, description, all/home/away, update); `group`/`all`/`goals.for` são
    keywords SQL — escapadas com crases no stg (precedente do team_season_stats).

    snapshot_date = data da coleta (UTC), mesma definição do team_season_stats. A API
    não tem histórico diário: o backfill captura a tabela FINAL de 2024/2025 com
    snapshot_date do dia do run; o `update` da API preserva o fim de temporada real.

    Modo "current" (default): ano corrente (Brasileirão 2026 + Copa 2026) — schedule diário.
    Modo "backfill": anos anteriores (Brasileirão 2024/2025) — one-shot manual.
    """

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="standings",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in ("current", "backfill"):
            raise ValueError(f"mode inválido: {mode}. Use 'current' ou 'backfill'.")
        self.mode = mode
        self.targets = STANDINGS_CURRENT if mode == "current" else STANDINGS_BACKFILL
        self.snapshot_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def extract(self, **kwargs) -> Dict[str, Any]:
        """Itera (liga, season) do mode e achata grupos × times em 1 linha por time."""
        rows = []
        empty = 0
        failed = 0

        for league_id, season in self.targets:
            try:
                envelope = self.client.get_standings(league_id, season)
                time.sleep(0.4)  # cortesia entre chamadas (rate-limit API-Football)

                errors = envelope.get("errors")
                if errors:
                    logger.error(
                        f"API errors p/ league={league_id} season={season}: {errors}"
                    )
                    failed += 1
                    continue

                resp = envelope.get("response") or []
                groups = (resp[0].get("league") or {}).get("standings") if resp else None
                if not groups:
                    # Ex.: Copa antes da tabela de grupos existir — segue sem falhar.
                    logger.info(
                        f"Sem standings p/ league={league_id} season={season} (pulando)."
                    )
                    empty += 1
                    continue

                count_before = len(rows)
                for group_entries in groups:
                    for entry in group_entries or []:
                        rows.append({
                            "requested_league_id": league_id,
                            "requested_season": season,
                            "snapshot_date": self.snapshot_date,
                            "loaded_at": datetime.utcnow().isoformat(),
                            **entry,
                        })
                logger.info(
                    f"league={league_id} season={season}: {len(groups)} grupo(s), "
                    f"{len(rows) - count_before} linhas."
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
            f"{empty} sem standings, {failed} com erro)."
        )
        return {"total_rows": len(rows), "standings": rows}

    def extract_and_save(self, **kwargs) -> str:
        """Override: arquivo date-stampado por mode (1 snapshot por dia no GCS)."""
        logger.info(
            f"Iniciando extração standings (mode={self.mode}, snapshot_date={self.snapshot_date})"
        )
        data = self.extract(**kwargs)

        if data.get("total_rows", 0) == 0:
            # Sem upload: um arquivo metadata-only viraria linha NULL na external table.
            logger.warning("Nenhuma linha coletada — upload pulado (sem arquivo vazio).")
            return ""

        gcs_path = self.storage.upload_json(
            data=data,
            endpoint="standings",
            season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
            sport="futebol",
            mode=self.mode,
            date=self.snapshot_date,
        )
        logger.info(f"Extração concluída: {gcs_path}")
        return gcs_path
