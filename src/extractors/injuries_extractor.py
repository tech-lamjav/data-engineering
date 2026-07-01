"""Extractor para /injuries da API-Football v3 (lesionados/suspensos)."""
import time
from datetime import datetime, timezone
from typing import Dict, Any, List
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient
from src.config import (
    INJURIES_BACKFILL,
    INJURIES_CURRENT,
    FUTEBOL_INJURIES_WINDOWS,
    FUTEBOL_INJURIES_LEAGUE_IDS,
    get_gcs_path,
)
from src.utils.logger import setup_logger
from src.utils.helpers import utcnow_iso

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

    Mode "pregame" (S7 do Motor de Score): coleta FORWARD-ONLY por fixture via
    /injuries?fixture={id} dos jogos NS futuros (FUTEBOL_INJURIES_WINDOWS), espelhando o
    PredictionsExtractor. O season-log (current/backfill) congela quando não há rodada
    recente (pausa FIFA) — não traz desfalques de jogos futuros; o endpoint por fixture
    sim. 1 arquivo date-stampado por (fixture, janela, dia):
    raw_futebol_injuries_{fixture}_daily_{YYYY-MM-DD}.json → skip-if-exists por DIA. Os
    caminhos current/backfill ficam INTACTOS (retornam str; pregame retorna List[str]).
    """

    VALID_MODES = ("current", "backfill", "pregame")

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="injuries",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in self.VALID_MODES:
            raise ValueError(
                f"mode inválido: {mode}. Use 'current', 'backfill' ou 'pregame'."
            )
        self.mode = mode
        self.snapshot_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if mode == "pregame":
            # Atributos de instância (não constantes diretas) p/ permitir override em testes.
            self.windows = dict(FUTEBOL_INJURIES_WINDOWS)
            self.league_ids = list(FUTEBOL_INJURIES_LEAGUE_IDS)
            self.targets = None
        else:
            self.targets = INJURIES_CURRENT if mode == "current" else INJURIES_BACKFILL

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
                        "loaded_at": utcnow_iso(),
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
        if failed:
            # Resumo de FALHA distinto de 'sem injuries': sinaliza erro real (quota/5xx/GCS).
            logger.error(
                f"RESUMO DE FALHA — injuries (mode={self.mode}): {failed} alvo(s) falharam "
                f"de {len(self.targets)}. Snapshot pode estar incompleto — re-executar."
            )
        return {"total_rows": len(rows), "injuries": rows}

    def extract_by_fixture(self, fixture_id: int, league_id: int, season: int) -> Dict[str, Any]:
        """Bate /injuries?fixture e achata 1 linha/jogador, carimbando como o season-log.

        Mesmo shape de linha de extract() (requested_league_id/season + snapshot_date +
        loaded_at + **item) p/ coexistir no mesmo raw_futebol_injuries (external table) e no
        fact_injuries_snapshot, SEM coluna collection_window (a janela vai só no nome do
        arquivo). requested_league_id/season vêm do FIXTURE (não da resposta) p/ alinhar a
        chave de dedup do fato com a do season-log (mesmo (league, season, snapshot_date)).
        """
        envelope = self.client.get_injuries_by_fixture(fixture_id)

        errors = envelope.get("errors")
        if errors:  # list vazia [] (sucesso) é falsy; só loga erro de verdade
            logger.error(f"API errors p/ fixture={fixture_id}: {errors}")
            return {"total_rows": 0, "injuries": []}

        resp = envelope.get("response") or []
        rows = [
            {
                "requested_league_id": league_id,
                "requested_season": season,
                "snapshot_date": self.snapshot_date,
                "loaded_at": utcnow_iso(),
                **item,
            }
            for item in resp
        ]
        return {"total_rows": len(rows), "injuries": rows}

    def extract_and_save(self, **kwargs):
        """Override: season-log date-stampado (current/backfill → str) OU pré-jogo por
        fixture (pregame → List[str], 1 arquivo/jogo, espelha o PredictionsExtractor)."""
        if self.mode == "pregame":
            return self._extract_and_save_pregame()

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

    def _extract_and_save_pregame(self) -> List[str]:
        """Coleta FORWARD-ONLY por fixture (modo pregame). Varre os jogos NS dentro do
        horizonte (FUTEBOL_INJURIES_WINDOWS), calcula o lead até o kickoff e, p/ a janela
        cuja banda contém o lead, bate /injuries?fixture 1x com skip-if-exists por (fixture,
        janela, dia). Espelha PredictionsExtractor.extract_and_save."""
        max_lead = max((hi for (_, hi) in self.windows.values()), default=0)
        logger.info(
            f"Iniciando extração injuries pré-jogo (janelas={self.windows}, ligas={self.league_ids})"
        )

        fixtures = self.storage.get_upcoming_fixtures_with_kickoff(max_lead)
        if not fixtures:
            logger.info("Nenhum jogo NS na janela de injuries — nada a coletar.")
            return []

        now = datetime.now(timezone.utc)
        today = self.snapshot_date  # date-stamp do snapshot (recaptura 1x/dia por janela)
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

            # Liga sem coverage.injuries (ex.: Copa) → fora dos targets: não gasta chamada.
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
                # raw_futebol_injuries_{fixture}_{janela}_{YYYY-MM-DD}.json
                blob_path = get_gcs_path(
                    "injuries", 0, sport="futebol", game_id=fixture_id,
                    mode=window, date=today,
                )

                # Erros transitórios (timeout de API/GCS) não abortam o run: loga, conta
                # como falha e segue; skip-if-exists re-tenta no próximo poll.
                try:
                    if self.storage.bucket.blob(blob_path).exists():
                        skipped += 1
                        continue

                    data = self.extract_by_fixture(
                        fixture_id=fixture_id,
                        league_id=league_id,
                        season=season,
                    )
                    time.sleep(0.4)  # cortesia entre chamadas (rate-limit API-Football)

                    if data.get("total_rows", 0) == 0:
                        # Sem desfalque registrado ainda: NÃO grava (re-tenta no próximo poll;
                        # gravar vazio travaria o skip-if-exists e viraria linha NULL).
                        empty += 1
                        continue

                    gcs_path = self.storage.upload_json(
                        data=data,
                        endpoint="injuries",
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
            f"injuries pré-jogo concluído: {len(saved_paths)} salvos, {skipped} pulados "
            f"(já existem), {empty} sem desfalque, {failed} com erro; {considered} "
            f"(fixture,janela) na banda."
        )
        if failed:
            # Resumo de FALHA distinto de 'sem desfalque': forward-only, janela pode ser perdida.
            logger.error(
                f"RESUMO DE FALHA — injuries pré-jogo: {failed} (fixture,janela) falharam de "
                f"{considered} na banda. Coleta forward-only — janela pode ter sido perdida. Investigar."
            )
        return saved_paths
