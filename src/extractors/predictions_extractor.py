"""Extractor para /predictions da API-Football v3 (previsão pré-jogo — baseline)."""
import time
from datetime import datetime, timezone
from typing import Dict, Any, List
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import (
    FUTEBOL_PREDICTIONS_WINDOWS,
    FUTEBOL_PREDICTIONS_LEAGUE_IDS,
    get_gcs_path,
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class PredictionsExtractor(BaseExtractor):
    """Extrai a previsão da própria API via /predictions?fixture={id} em 1 janela (T-2h).

    BASELINE DE COMPARAÇÃO (não é produto): guarda as probabilidades (predictions.percent)
    e a comparação de força (comparison.*) que o algoritmo da API calcula por jogo, p/
    avaliar depois se um modelo próprio bate a API consistentemente (= edge real).

    Coleta FORWARD-ONLY (previsão de jogo passado não é reconstruível — a API recomputa
    com o resultado já conhecido). Um poll (~15min) faz UMA passada nos jogos NS das
    próximas ~2h, calcula o lead (minutos até o kickoff) e, p/ a janela cuja banda
    (FUTEBOL_PREDICTIONS_WINDOWS) contém o lead, bate /predictions 1x e grava 1 arquivo
    por fixture. skip-if-exists trava recaptura (1 snapshot/jogo). NÃO grava vazio (jogo
    sem previsão ainda → re-tenta no próximo poll; gravar vazio travaria o skip-if-exists).

    1 linha NDJSON por fixture; predictions/comparison ficam aninhados (flatten no dbt,
    sem UNNEST — 1 previsão por jogo).
    """

    def __init__(self):
        super().__init__(
            endpoint_name="predictions",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        # Atributos de instância (não constantes diretas) p/ permitir override em testes.
        self.windows = dict(FUTEBOL_PREDICTIONS_WINDOWS)
        self.league_ids = list(FUTEBOL_PREDICTIONS_LEAGUE_IDS)

    def extract(
        self,
        fixture_id: int,
        collection_window: str,
        kickoff_ts: int,
        league_id: int,
        season: int,
        **kwargs,
    ) -> Dict[str, Any]:
        """Bate /predictions p/ um fixture e monta 1 bloco (predictions/comparison aninhados).

        Carimba os metadados do snapshot (janela, timestamps, kickoff). O /predictions
        devolve 1 elemento em `response` (não pagina); guardamos só predictions+comparison
        (teams/league/h2h são redundantes com fact_team_season_stats/fact_fixtures/fact_h2h).
        """
        envelope = self.client.get_predictions(fixture_id)

        errors = envelope.get("errors")
        if errors:  # list vazia [] (sucesso) é falsy; só loga se vier erro de verdade
            logger.error(f"API errors para fixture={fixture_id}: {errors}")
            return {"total_predictions": 0}

        response = envelope.get("response", []) or []
        if not response:
            # Sem previsão publicada ainda: NÃO grava (re-tenta no próximo poll).
            return {"total_predictions": 0}

        item = response[0]
        now_iso = datetime.now(timezone.utc).isoformat()

        return {
            "total_predictions": 1,
            "fixture_id": fixture_id,
            "league_id": league_id,
            "season": season,
            "collection_window": collection_window,
            "collection_timestamp": now_iso,
            "kickoff_timestamp": kickoff_ts,
            "loaded_at": now_iso,
            "predictions": item.get("predictions"),
            "comparison": item.get("comparison"),
        }

    def extract_and_save(self, **kwargs) -> List[str]:
        """Bucketa os jogos NS na janela T-2h, aplica skip-if-exists e salva 1 arquivo/jogo."""
        max_lead = max((hi for (_, hi) in self.windows.values()), default=0)
        logger.info(
            f"Iniciando extração predictions (janelas={self.windows}, ligas={self.league_ids})"
        )

        fixtures = self.storage.get_upcoming_fixtures_with_kickoff(max_lead)
        if not fixtures:
            logger.info("Nenhum jogo NS na janela de predictions — nada a coletar.")
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

            # Liga sem coverage.predictions → fora dos targets: não gasta chamada.
            if league_id not in self.league_ids or kickoff_ts is None:
                continue

            lead_min = (
                datetime.fromtimestamp(kickoff_ts, tz=timezone.utc) - now
            ).total_seconds() / 60.0

            for window, (lo, hi) in self.windows.items():
                if not (lo <= lead_min <= hi):
                    continue
                considered += 1

                # 1 arquivo por fixture (janela única, sem sufixo de mode):
                # raw_futebol_predictions_{fixture}.json
                blob_path = get_gcs_path(
                    "predictions", 0, sport="futebol", game_id=fixture_id,
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

                    if data.get("total_predictions", 0) == 0:
                        # Sem previsão ainda: NÃO grava, p/ ser re-tentado no próximo poll.
                        logger.info(
                            f"Fixture {fixture_id} ({window}): sem previsão, pulando (re-tenta depois)."
                        )
                        empty += 1
                        continue

                    gcs_path = self.storage.upload_json(
                        data=data,
                        endpoint="predictions",
                        season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
                        sport="futebol",
                        game_id=fixture_id,
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
            f"predictions concluído: {len(saved_paths)} salvos, {skipped} pulados (já existem), "
            f"{empty} sem previsão, {failed} com erro; {considered} (fixture,janela) na banda."
        )
        return saved_paths
