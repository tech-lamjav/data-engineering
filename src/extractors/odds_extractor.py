"""Extractor para /odds da API-Football v3 (odds pré-jogo por casa/mercado)."""
import time
from datetime import datetime, timezone
from typing import Dict, Any, List
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import (
    FUTEBOL_ODDS_WINDOWS,
    FUTEBOL_ODDS_WINDOWS_DIARIAS,
    FUTEBOL_ODDS_LEAGUE_IDS,
    FUTEBOL_ODDS_DAILY_BUCKET_HOURS,
    get_gcs_path,
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def _daily_bucket_stamp(now: datetime, bucket_hours: int) -> str:
    """Date-stamp da janela DIÁRIA, com o dia partido em blocos de `bucket_hours` (PPP#366).

    Função pura (sem `self`) pra ser testável sem mockar o relógio: quem chama passa o
    `now` que quiser. O bloco é o piso da hora arredondado pra baixo em múltiplos de
    `bucket_hours` — ex. bucket_hours=6: 00:00-05:59 → "00h", 06:00-11:59 → "06h", etc.
    Duas chamadas no MESMO bloco devolvem o MESMO stamp (skip-if-exists trava); bloco
    diferente, stamp diferente (destrava a recaptura). Ver FUTEBOL_ODDS_DAILY_BUCKET_HOURS
    pro porquê do número não poder ser pequeno demais.
    """
    bloco = (now.hour // bucket_hours) * bucket_hours
    return f"{now:%Y-%m-%d}_{bloco:02d}h"


class OddsExtractor(BaseExtractor):
    """Extrai odds pré-jogo via /odds?fixture={id} em 4 janelas (diária + 3 de fechamento).

    Coleta FORWARD-ONLY (não dá pra reconstruir as janelas de jogos passados). Um poll
    (~15min) faz UMA passada nos jogos NS dentro do horizonte, calcula o lead (minutos até o
    kickoff) e, p/ cada janela cuja banda (FUTEBOL_ODDS_WINDOWS) contém o lead, bate /odds
    1x e grava 1 arquivo. NÃO grava vazio (jogo sem odds publicadas ainda → re-tenta no
    próximo poll; gravar vazio travaria o skip-if-exists — mesma lição das escalações
    pré-jogo, e diferente do vazio registrado de injuries, cuja banda é diária e não de
    fechamento).

    DUAS naturezas de janela:
      - "daily" (>24h até o horizonte de 7 dias): o path é DATE-STAMPADO
        (raw_futebol_odds_{fixture}_daily_{YYYY-MM-DD}_{HHh}.json — PPP#366), logo
        skip-if-exists é por (fixture, janela, BLOCO de FUTEBOL_ODDS_DAILY_BUCKET_HOURS
        horas) → N capturas/dia enquanto o jogo fica na banda, não mais 1. É o que tira o
        board do recorte de 24h. Mesmo arquétipo do PredictionsExtractor.
      - t24h / t1h / t15m (fechamento): sem date-stamp, 1 captura única por (fixture, janela).
        t15m é a linha de fechamento p/ CLV. Nomes de arquivo INTACTOS.

    ⚠️ As bandas são DISJUNTAS por requisito — ver FUTEBOL_ODDS_WINDOWS. Sobreposição faria a
    mesma passada bucketar o mesmo fixture duas vezes: duas chamadas, dois arquivos, duas
    linhas no fato com rótulos diferentes p/ o mesmo preço.

    ⚠️ O fato a jusante (fact_odds_snapshot) dedup latest-wins por (fixture, casa, mercado,
    outcome, collection_window) — SEM o dia. Enquanto o grão de lá não mudar
    (analytics-engineering#37), as N capturas diárias de um fixture colapsam na mais recente
    no fato. Os arquivos ficam todos na landing: o histórico de movimento de linha está
    guardado e é recuperável quando o grão mudar; é justamente por ser forward-only que vale
    coletar agora.

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
        self.daily_windows = set(FUTEBOL_ODDS_WINDOWS_DIARIAS)
        self.league_ids = list(FUTEBOL_ODDS_LEAGUE_IDS)
        self.daily_bucket_hours = FUTEBOL_ODDS_DAILY_BUCKET_HOURS

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
        # date-stamp das janelas diárias, em blocos de FUTEBOL_ODDS_DAILY_BUCKET_HOURS (PPP#366)
        daily_stamp = _daily_bucket_stamp(now, self.daily_bucket_hours)
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

                # Só as janelas diárias date-stampam → skip-if-exists por (fixture, janela,
                # BLOCO de daily_bucket_hours horas), N capturas/dia enquanto o fixture fica
                # na banda. As de fechamento seguem sem data no nome: 1 captura única, e é
                # assim que o fato já lê.
                date_stamp = daily_stamp if window in self.daily_windows else None

                blob_path = get_gcs_path(
                    "odds", 0, sport="futebol", game_id=fixture_id, mode=window,
                    date=date_stamp,
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

                    tem_odds = data.get("total_bookmakers", 0) > 0

                    if not tem_odds and date_stamp is None:
                        # Banda de FECHAMENTO sem odds publicadas: NÃO grava, p/ ser
                        # re-tentada no próximo poll. A banda tem minutos de largura e a
                        # casa pode publicar a qualquer momento; gravar aqui travaria o
                        # skip-if-exists e perderia a linha de fechamento, que é
                        # forward-only e não se reconstrói.
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
                        mode=window,  # sufixo _daily | _t24h | _t1h | _t15m no nome
                        date=date_stamp,  # só as diárias (None nas de fechamento)
                    )

                    if tem_odds:
                        saved_paths.append(gcs_path)
                    else:
                        # VAZIO REGISTRADO na janela diária. Sem ele a banda diária é uma
                        # bomba de cota: ela tem DIAS de largura, então um fixture sem odds
                        # nunca gravaria arquivo, o skip-if-exists nunca travaria, e o poll
                        # de 15min reperguntaria o mesmo vazio ~96x/dia por até uma semana.
                        # Liga dormente (coverage.odds=FALSE até a abertura) devolve vazio
                        # de propósito — são 5 delas armadas hoje. Com o arquivo, no máximo
                        # 24/daily_bucket_hours capturas/dia (PPP#366) em vez de 96 — é o
                        # bucket, não o vazio registrado, quem trava o pior caso agora.
                        # Fora de saved_paths: arquivo sem casa nenhuma não gera linha no
                        # fato (o UNNEST de `bets` vazio elimina a linha no staging), então
                        # não há rebuild de dbt a fazer.
                        empty += 1
                except Exception as e:
                    logger.error(
                        f"Erro ao processar fixture {fixture_id} ({window}): {str(e)}",
                        exc_info=True,
                    )
                    failed += 1
                    continue

        logger.info(
            f"odds concluído: {len(saved_paths)} com odds, {empty} sem odds (vazio "
            f"registrado na diária, re-tenta nas de fechamento), {skipped} pulados "
            f"(já existem), {failed} com erro; {considered} (fixture,janela) na banda."
        )
        if failed:
            # Resumo de FALHA distinto de 'sem odds': odds é FORWARD-ONLY — uma falha
            # aqui pode perder a janela de fechamento permanentemente. Loga ERROR p/ o
            # resumo diário não confundir com 'jogo sem odds publicadas ainda'.
            logger.error(
                f"RESUMO DE FALHA — odds: {failed} (fixture,janela) falharam de {considered} na banda. "
                "Coleta forward-only — janela pode ter sido perdida. Investigar."
            )
        return saved_paths
