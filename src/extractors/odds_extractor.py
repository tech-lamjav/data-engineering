"""Extractor para /odds da API-Football v3 (odds pré-jogo por casa/mercado)."""
import time
from datetime import datetime, timezone
from typing import Dict, Any, List
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import FUTEBOL_ODDS_WINDOWS, FUTEBOL_ODDS_LEAGUE_IDS, get_gcs_path
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class OddsExtractor(BaseExtractor):
    """Extrai odds pré-jogo via /odds?fixture={id} em 2 janelas (T-24h e T-1h).

    Coleta FORWARD-ONLY (não dá pra reconstruir as janelas de jogos passados). Um poll
    (~15min) faz UMA passada nos jogos NS das próximas ~24h, calcula o lead (minutos até o
    kickoff) e, p/ cada janela cuja banda (FUTEBOL_ODDS_WINDOWS) contém o lead, bate /odds
    1x e grava 1 arquivo por (fixture, janela). skip-if-exists trava recaptura → 1 snapshot
    por janela. NÃO grava vazio (jogo sem odds publicadas ainda → re-tenta no próximo poll;
    gravar vazio travaria o skip-if-exists — mesma lição das escalações pré-jogo).

    Guarda TODAS as casas/mercados que a API devolve (filtrar não economiza quota — o custo
    é a chamada); o afunilamento p/ os mercados-alvo acontece no dbt (fact_odds_snapshot).

    1 linha NDJSON por (fixture, janela, casa); bets/values ficam aninhados (UNNEST no dbt).
    """

    def __init__(self):
        super().__init__(
            endpoint_name="odds",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        # Atributos de instância (não constantes diretas) p/ permitir override em testes.
        self.windows = dict(FUTEBOL_ODDS_WINDOWS)
        self.league_ids = list(FUTEBOL_ODDS_LEAGUE_IDS)

    def extract(
        self,
        fixture_id: int,
        collection_window: str,
        kickoff_ts: int,
        league_id: int,
        season: int,
        **kwargs,
    ) -> Dict[str, Any]:
        """Bate /odds p/ um fixture e monta 1 bloco por casa (bets/values aninhados).

        Carimba os metadados do snapshot (janela, timestamps, kickoff) que o array_key
        "odds" replica em cada linha NDJSON (1 por casa).
        """
        envelope = self.client.get_odds(fixture_id)

        errors = envelope.get("errors")
        if errors:  # list vazia [] (sucesso) é falsy; só loga se vier erro de verdade
            logger.error(f"API errors para fixture={fixture_id}: {errors}")
            return {"total_bookmakers": 0, "odds": []}

        response = envelope.get("response", []) or []
        now_iso = datetime.now(timezone.utc).isoformat()
        api_update = response[0].get("update") if response else None

        blocks = []
        for item in response:
            for bm in (item.get("bookmakers") or []):
                blocks.append({
                    "bookmaker_id": bm.get("id"),
                    "bookmaker_name": bm.get("name"),
                    "bets": bm.get("bets") or [],
                })

        return {
            "total_bookmakers": len(blocks),
            "fixture_id": fixture_id,
            "league_id": league_id,
            "season": season,
            "collection_window": collection_window,
            "collection_timestamp": now_iso,
            "kickoff_timestamp": kickoff_ts,
            "api_update": api_update,
            "loaded_at": now_iso,
            "odds": blocks,
        }

    def extract_and_save(self, **kwargs) -> List[str]:
        """Bucketa os jogos NS nas janelas, aplica skip-if-exists e salva 1 arquivo/jogo/janela."""
        max_lead = max((hi for (_, hi) in self.windows.values()), default=0)
        logger.info(
            f"Iniciando extração odds (janelas={self.windows}, ligas={self.league_ids})"
        )

        fixtures = self.storage.get_upcoming_fixtures_with_kickoff(max_lead)
        if not fixtures:
            logger.info("Nenhum jogo NS na janela de odds — nada a coletar.")
            return []

        now = datetime.now(timezone.utc)
        saved_paths = []
        skipped = 0
        empty = 0
        failed = 0
        considered = 0

        for fx in fixtures:
            fixture_id = fx["fixture_id"]
            league_id = fx.get("league_id")
            season = fx.get("season")
            kickoff_ts = fx.get("kickoff_ts")

            # Liga sem coverage.odds → fora dos targets: não gasta chamada.
            if league_id not in self.league_ids or kickoff_ts is None:
                continue

            lead_min = (
                datetime.fromtimestamp(kickoff_ts, tz=timezone.utc) - now
            ).total_seconds() / 60.0

            for window, (lo, hi) in self.windows.items():
                if not (lo <= lead_min <= hi):
                    continue
                considered += 1

                blob_path = get_gcs_path(
                    "odds", 0, sport="futebol", game_id=fixture_id, mode=window,
                )

                # Erros transitórios (timeout de API/GCS) não abortam o run: loga, conta
                # como falha e segue; skip-if-exists re-tenta no próximo poll.
                try:
                    if self.storage.bucket.blob(blob_path).exists():
                        skipped += 1
                        continue

                    data = self.extract(
                        fixture_id=fixture_id,
                        collection_window=window,
                        kickoff_ts=kickoff_ts,
                        league_id=league_id,
                        season=season,
                    )
                    time.sleep(0.4)  # cortesia entre chamadas (rate-limit API-Football)

                    if data.get("total_bookmakers", 0) == 0:
                        # Sem odds publicadas ainda (ou jogo sem mercado): NÃO grava, p/
                        # ser re-tentado no próximo poll. Gravar vazio aqui faria o
                        # skip-if-exists bloquear as próximas passadas da janela.
                        logger.info(
                            f"Fixture {fixture_id} ({window}): sem odds, pulando (re-tenta depois)."
                        )
                        empty += 1
                        continue

                    gcs_path = self.storage.upload_json(
                        data=data,
                        endpoint="odds",
                        season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
                        sport="futebol",
                        game_id=fixture_id,
                        mode=window,  # sufixo _t24h | _t1h no nome do arquivo
                    )
                    saved_paths.append(gcs_path)
                except Exception as e:
                    logger.error(
                        f"Erro ao processar fixture {fixture_id} ({window}): {str(e)}",
                        exc_info=True,
                    )
                    failed += 1
                    continue

        logger.info(
            f"odds concluído: {len(saved_paths)} salvos, {skipped} pulados (já existem), "
            f"{empty} sem odds, {failed} com erro; {considered} (fixture,janela) na banda."
        )
        return saved_paths
