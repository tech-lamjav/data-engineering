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
    """Extrai a previsão da própria API via /predictions?fixture={id} em 2 janelas.

    BASELINE DE COMPARAÇÃO + fonte da corroboração `modelo_api_concorda` (+7) do Motor de
    Score: guarda as probabilidades (predictions.percent) e a comparação de força
    (comparison.*) que o algoritmo da API calcula por jogo.

    Coleta FORWARD-ONLY (previsão de jogo passado não é reconstruível — a API recomputa
    com o resultado já conhecido). Um poll (~15min) faz UMA passada nos jogos NS dentro do
    horizonte (FUTEBOL_PREDICTIONS_WINDOWS; hoje "daily" = ~2h a 14 dias + "t2h" perto do
    jogo), calcula o lead (min até o kickoff) e, p/ a janela cuja banda contém o lead,
    bate /predictions 1x.

    O path é DATE-STAMPADO por (fixture, janela, dia):
    raw_futebol_predictions_{fixture}_{janela}_{YYYY-MM-DD}.json. Logo skip-if-exists é por
    DIA → a janela "daily" recaptura 1x/dia (mantém os jogos futuros atualizados); a "t2h"
    dá o refresh perto do kickoff. O fato dedup latest-wins por loaded_at → o snapshot mais
    fresco vence; os arquivos acumulam no GCS (padrão de standings/injuries). NÃO grava vazio
    (jogo sem previsão ainda — ex.: liga sem coverage ou mata-mata com times TBD → degradação
    graciosa: re-tenta no próximo poll; gravar vazio travaria o skip-if-exists).

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
        today = now.strftime("%Y-%m-%d")  # date-stamp do snapshot (recaptura 1x/dia por janela)
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

                # 1 arquivo por (fixture, janela, dia) — date-stampado p/ recaptura diária:
                # raw_futebol_predictions_{fixture}_{janela}_{YYYY-MM-DD}.json
                blob_path = get_gcs_path(
                    "predictions", 0, sport="futebol", game_id=fixture_id,
                    mode=window, date=today,
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
                        mode=window,
                        date=today,
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
        if failed:
            # Resumo de FALHA distinto de 'sem previsão': forward-only, janela pode ser perdida.
            logger.error(
                f"RESUMO DE FALHA — predictions: {failed} (fixture,janela) falharam de {considered} na banda. "
                "Coleta forward-only — janela pode ter sido perdida. Investigar."
            )
        return saved_paths
