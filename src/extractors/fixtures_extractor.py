"""Extractor para /fixtures da API-Football v3 (tabela mãe de jogos)."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from src.utils.helpers import utcnow_iso
from src.extractors.base_extractor import BaseExtractor
from src.clients.api_football_client import ApiFootballClient, FIXTURES_BY_IDS_MAX_BATCH
from src.config import FIXTURES_BACKFILL, FIXTURES_CURRENT, FUTEBOL_STATUS_TERMINAL
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


# --------------------------------------------------------------------------------------- #
# Funções puras (DE#60 — cadência da coleta de placar). Sem GCS, sem API: operam sobre
# listas de rows já lidas (mesma forma gravada por FixturesExtractor.extract() /
# GCSStorage.get_fixture_rows_from_storage), testáveis sem rede.
# --------------------------------------------------------------------------------------- #

def _fixture_id(row: Dict[str, Any]) -> Optional[int]:
    return (row.get("fixture") or {}).get("id")


def _kickoff(row: Dict[str, Any]) -> Optional[datetime]:
    ts = (row.get("fixture") or {}).get("timestamp")
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _status_short(row: Dict[str, Any]) -> Optional[str]:
    # `or {}` nos DOIS níveis: `fixture.status` pode vir explicitamente `null` (chave
    # presente, valor None) — `.get("status", {})` só aplicaria o default se a chave
    # estivesse AUSENTE, e um `.get("short")` sobre None quebraria o ciclo de poll inteiro.
    return ((row.get("fixture") or {}).get("status") or {}).get("short")


def select_live_candidates(
    rows: List[Dict[str, Any]],
    now: datetime,
    lookback_hours: int = 12,
) -> List[int]:
    """Fixture_ids que merecem refresh: kickoff em [now − lookback_hours, now] e status
    AINDA não-terminal no melhor dado conhecido (`rows` pode misturar `_current.json` +
    `_live.json` anterior — quem chama decide a precedência).

    Não depende de nenhuma suposição sobre `?live=all` "segurar" o jogo por mais um ciclo:
    um jogo que já devia ter status novo (kickoff passou) e ainda está marcado como não-
    terminal é candidato a ser perguntado direto por `?ids=`.
    """
    lookback = now - timedelta(hours=lookback_hours)
    candidates = []
    for row in rows:
        fixture_id = _fixture_id(row)
        kickoff = _kickoff(row)
        if fixture_id is None or kickoff is None:
            continue
        if _status_short(row) in FUTEBOL_STATUS_TERMINAL:
            continue
        if not (lookback <= kickoff <= now):
            continue
        candidates.append(fixture_id)
    return candidates


def filter_tracked_fixtures(
    rows: List[Dict[str, Any]], tracked_ids: Set[int]
) -> List[Dict[str, Any]]:
    """Filtra `rows` (ex.: resposta global de `?live=all`, que cobre o mundo inteiro) para
    só os fixture_ids que já existem no nosso próprio snapshot (FIXTURES_CURRENT)."""
    return [row for row in rows if _fixture_id(row) in tracked_ids]


def merge_fixture_rows(
    existing_rows: List[Dict[str, Any]],
    fresh_rows: List[Dict[str, Any]],
    now: datetime,
    max_age_hours: int = 72,
) -> List[Dict[str, Any]]:
    """Funde `fresh_rows` (recém-buscados) sobre `existing_rows` (o `_live.json` do ciclo
    anterior), latest-per-fixture_id (fresh sempre vence), podado por idade do kickoff.

    Sem isto, um fixture que terminou (FT) e no ciclo seguinte deixa de ser candidato (ver
    select_live_candidates) desapareceria do `_live.json` — o QUALIFY do fact_fixtures
    voltaria a escolher a linha velha do `_current.json`, e o status oscilaria (pior que
    hoje). `max_age_hours` evita que o arquivo cresça sem limite.
    """
    by_id: Dict[int, Dict[str, Any]] = {}
    for row in existing_rows:
        fixture_id = _fixture_id(row)
        if fixture_id is not None:
            by_id[fixture_id] = row
    for row in fresh_rows:
        fixture_id = _fixture_id(row)
        if fixture_id is not None:
            by_id[fixture_id] = row  # fresh sempre vence, mesmo id já existindo

    # Fail-closed na poda: linha sem timestamp (malformada) não tem idade avaliável e é
    # DESCARTADA, não mantida para sempre — um `or now` aqui compararia sempre "fresca" e
    # o arquivo cresceria sem limite com lixo que nunca se prova velho o suficiente.
    cutoff = now - timedelta(hours=max_age_hours)
    kept = []
    for row in by_id.values():
        kickoff = _kickoff(row)
        if kickoff is not None and kickoff >= cutoff:
            kept.append(row)
    return kept


# --------------------------------------------------------------------------------------- #
# Funções puras (DE#93 — universo dos amistosos). Origem: ADR 0004 (consequência de desenho
# 2) e `docs/SELECOES_NATIONS_LEAGUE_AMISTOSOS.md` §4.0, linhas 5 (universo), 10 (append-
# only) e 13 (guarda de crescimento) — a numeração 5/10/13 é da TABELA do §4.0, não da lista
# 1-5 da própria ADR. Sem GCS, sem API: dado um conjunto de team_ids já conhecidos por outras
# competições, decidem que jogos de amistoso entram no histórico e como esse conjunto evolui
# entre execuções. A regra não interpreta nome de time nem confia no campo `national` da API
# — ele não funciona (São Tomé vem `false`, Albania U21 vem `true`). De onde vem
# `known_team_ids` (o arquivo de teams no GCS, linha 13) e a fiação no extrator ficam para o
# DE#94/#95; aqui só a regra, sem efeito colateral, chamada por ninguém ainda.
# --------------------------------------------------------------------------------------- #

def _fixture_team_ids(row: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    teams = row.get("teams") or {}
    home_id = (teams.get("home") or {}).get("id")
    away_id = (teams.get("away") or {}).get("id")
    return home_id, away_id


def filter_amistosos_universo(
    rows: List[Dict[str, Any]], known_team_ids: Set[int]
) -> List[Dict[str, Any]]:
    """Recorte do universo de amistosos (ADR 0004, consequência 2; SELECOES §4.0 linha 5):
    só entra o jogo em que os DOIS times já são conhecidos por outra competição do pipeline.
    Jogo com um só time conhecido — ou nenhum — fica de fora; não há meio-termo.

    `known_team_ids` já deve vir unido (ver `merge_known_team_ids`) — esta função só lê.
    """
    universo = []
    for row in rows:
        home_id, away_id = _fixture_team_ids(row)
        if home_id is None or away_id is None:
            continue
        if home_id in known_team_ids and away_id in known_team_ids:
            universo.append(row)
    return universo


def merge_known_team_ids(
    previous_ids: Optional[Set[int]], new_ids: Set[int]
) -> Set[int]:
    """União append-only (ADR 0004, consequência 2; SELECOES §4.0 linha 10): time que entrou
    no conjunto nunca sai, mesmo que deixe de aparecer em `new_ids` — senão remover uma
    competição da config encolheria o histórico de amistosos para trás. `previous_ids` pode
    ser `None`/vazio na primeira execução (bootstrap); o resultado nesse caso é só `new_ids`.
    Aceita qualquer iterável (ex.: `list` vindo de JSON) em ambos os parâmetros."""
    return set(previous_ids or set()) | set(new_ids)


def evaluate_known_teams_growth(
    previous_ids: Optional[Set[int]],
    merged_ids: Set[int],
    max_growth: int = 20,
) -> Dict[str, Any]:
    """Guarda de crescimento (SELECOES §4.0 linha 13) entre duas execuções do universo:
    quantos team_ids novos entraram, se o salto passa de `max_growth`, e se algum time do
    conjunto anterior sumiu de `merged_ids` — o que violaria o append-only que
    `merge_known_team_ids` promete (ADR 0004, consequência 2) e só pode acontecer se quem
    chamar passar aqui algo diferente do resultado daquela função.

    `previous_ids`/`merged_ids` aceitam qualquer iterável (ex.: `list` vindo de JSON), não
    só `set`. Sem `previous_ids` (bootstrap, primeira execução) não há baseline para
    comparar contra — `anomalous` fica sempre `False` e `is_bootstrap` marca o caso, para
    quem chama decidir se quer logar diferente. O que esta guarda vigia é o salto (ou o
    encolhimento) entre execuções normais, não o tamanho absoluto do primeiro conjunto."""
    previous = set(previous_ids or set())
    merged = set(merged_ids)
    added = merged - previous
    removed = previous - merged
    is_bootstrap = not previous
    return {
        "previous_count": len(previous),
        "merged_count": len(merged),
        "added_count": len(added),
        "added_ids": sorted(added),
        "removed_ids": sorted(removed),
        "is_bootstrap": is_bootstrap,
        "anomalous": (not is_bootstrap) and (len(added) > max_growth or bool(removed)),
    }


class FixturesExtractor(BaseExtractor):
    """Extrai jogos via /fixtures?league=&season= (paginado).

    Modo "current" (default): ano corrente — usado pelo schedule diário para
    atualizar status/placar de jogos em andamento e finalizados.
    Modo "backfill": anos anteriores — one-shot manual.
    Modo "live" (DE#60): atualiza status/placar SEM varrer a temporada inteira — poll de
    alta frequência (candidato a 15min) que grava `raw_futebol_fixtures_live.json`, um
    arquivo-fase irmão de `_current.json`/`_backfill.json` na mesma pasta (a external table
    já é wildcard sobre a pasta; `fact_fixtures` já faz QUALIFY latest-per-fixture_id — ver
    ADR/DE#60). NÃO itera FIXTURES_CURRENT/FIXTURES_BACKFILL: usa `?live=all` + `?ids=` em
    vez de `?league=&season=`.

    1 linha por fixture (jogo) requisitada. fixture_id é a chave que destrava
    todos os endpoints de jogo (stats, events, lineups, player_stats).
    """

    def __init__(self, mode: str = "current"):
        super().__init__(
            endpoint_name="fixtures",
            client=ApiFootballClient(),
            sport="futebol",
        )
        self.has_date = False
        if mode not in ("current", "backfill", "live"):
            raise ValueError(f"mode inválido: {mode}. Use 'current', 'backfill' ou 'live'.")
        self.mode = mode
        self.targets = (
            FIXTURES_CURRENT if mode == "current"
            else FIXTURES_BACKFILL if mode == "backfill"
            else None  # "live" não itera targets — ver extract_live()
        )
        self.last_fresh_count = 0  # populado por extract_live(); ver ali o motivo
        self.last_quota_remaining = None  # idem — DE#80, cota restante do header de live=all

    def extract(self, **kwargs) -> Dict[str, Any]:
        """Itera sobre os targets e mescla resposta + metadata da request.

        `failed_targets` separa 'liga falhou' (errors no envelope) de 'liga sem dados
        legitimamente' (response vazio sem errors) — extract_and_save usa isso p/ NÃO
        sobrescrever o arquivo bom no GCS quando um target obrigatório falha.
        """
        fixtures = []
        failed_targets = []
        for league_id, season in self.targets:
            logger.info(f"Extraindo league={league_id} season={season}...")
            envelope = self.client.get_fixtures(league_id, season)

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
                fixtures.append({
                    "requested_league_id": league_id,
                    "requested_season": season,
                    "loaded_at": utcnow_iso(),
                    **item,  # fixture: {...}, league: {...}, teams: {...}, goals, score
                })

        logger.info(
            f"Coletadas {len(fixtures)} linhas (mode={self.mode}, targets={len(self.targets)})"
        )
        return {
            "mode": self.mode,
            "total_fixtures": len(fixtures),
            "failed_targets": failed_targets,
            "fixtures": fixtures,
        }

    def extract_live(
        self,
        lookback_hours: int = 12,
        max_age_hours: int = 72,
    ) -> Dict[str, Any]:
        """Atualiza status/placar dos fixtures em andamento/recém-encerrados, sem varrer a
        temporada inteira (DE#60). Duas fontes, combinadas:

        - `get_fixtures_live()` (`?live=all`): 1 chamada, jogos em andamento GLOBALMENTE —
          filtrada para os fixture_ids que já conhecemos (o `_current.json`).
        - `get_fixtures_by_ids(candidatos)`: só para fixtures com kickoff recente e status
          ainda não-terminal no MELHOR dado conhecido (current + `_live.json` anterior) que
          `?live=all` não trouxe de volta — pega o FT final de quem já saiu do ar.

        O resultado é o merge com o `_live.json` do ciclo anterior (retém FT já visto),
        podado por idade — ver `merge_fixture_rows`. Erro de API em qualquer uma das duas
        chamadas levanta (mesmo espírito defensivo do modo current/backfill): não escrever
        um `_live.json` parcial por cima de um bom.

        CUSTO CONHECIDO, NÃO OTIMIZADO DE PROPÓSITO: cada ciclo baixa e faz parse do
        `_current.json` inteiro (todas as ligas rastreadas) só para montar `tracked_ids`.
        Numa cadência de 15min isso é ~96 downloads/dia de um arquivo texto — barato o
        bastante para não valer cache sem medir primeiro (DE#60 Fase B/C decide se isso
        precisa de otimização, com número real de latência/custo em mãos).
        """
        now = datetime.now(timezone.utc)
        current_rows = self.storage.get_fixture_rows_from_storage("current")
        previous_live_rows = self.storage.get_fixture_rows_from_storage("live")
        tracked_ids = {
            fid for fid in (_fixture_id(row) for row in current_rows) if fid is not None
        }

        live_envelope = self.client.get_fixtures_live()
        live_errors = live_envelope.get("errors")
        if live_errors:
            raise RuntimeError(f"fixtures (mode=live, live=all): API errors: {live_errors}")
        # DE#80: exposto p/ quem chama (cloud_run/futebol/extract_fixtures/main.py) — vira o
        # dado bruto que o resumo diário usa pra saber o consumo de cota perto do fim do dia,
        # de graça (a chamada já acontece; só o header estava sendo descartado). None se o
        # envelope não trouxer a chave (formato antigo/degradado) — nunca levanta por isso.
        self.last_quota_remaining = live_envelope.get("quota_remaining")

        fresh_rows = [
            {
                "requested_league_id": (item.get("league") or {}).get("id"),
                "requested_season": (item.get("league") or {}).get("season"),
                "loaded_at": utcnow_iso(),
                **item,
            }
            for item in (live_envelope.get("response") or [])
        ]
        fresh_rows = filter_tracked_fixtures(fresh_rows, tracked_ids)

        # Candidatos: melhor dado conhecido por fixture_id (fresh > live anterior > current,
        # nessa ordem de precedência — dict sobrescreve na ordem de inserção).
        known_by_id: Dict[int, Dict[str, Any]] = {}
        for row in current_rows + previous_live_rows + fresh_rows:
            fid = _fixture_id(row)
            if fid is not None:
                known_by_id[fid] = row
        candidate_ids = select_live_candidates(
            list(known_by_id.values()), now=now, lookback_hours=lookback_hours
        )
        already_fresh_ids = {_fixture_id(row) for row in fresh_rows}
        ids_to_query = [fid for fid in candidate_ids if fid not in already_fresh_ids]

        # Fatiado em lotes de FIXTURES_BY_IDS_MAX_BATCH (20, confirmado contra a API viva
        # em 2026-08-31 — acima disso ela rejeita a chamada inteira com erro de parâmetro).
        for i in range(0, len(ids_to_query), FIXTURES_BY_IDS_MAX_BATCH):
            batch = ids_to_query[i : i + FIXTURES_BY_IDS_MAX_BATCH]
            ids_envelope = self.client.get_fixtures_by_ids(batch)
            ids_errors = ids_envelope.get("errors")
            if ids_errors:
                raise RuntimeError(
                    f"fixtures (mode=live, ids={batch}): API errors: {ids_errors}"
                )
            # DE#80, achado do code-review: get_fixtures_by_ids também consome cota e
            # devolve um quota_remaining mais recente que o de get_fixtures_live() lá
            # em cima — sem isto, ciclo com candidato (comum perto de kickoff/apito,
            # justo quando o volume de chamada é maior) reportava cota "sobrando" que já
            # tinha sido gasta neste mesmo ciclo. Os lotes rodam em ordem — o último a
            # responder é o mais fresco.
            if ids_envelope.get("quota_remaining") is not None:
                self.last_quota_remaining = ids_envelope["quota_remaining"]
            fresh_rows.extend(
                {
                    "requested_league_id": (item.get("league") or {}).get("id"),
                    "requested_season": (item.get("league") or {}).get("season"),
                    "loaded_at": utcnow_iso(),
                    **item,
                }
                for item in (ids_envelope.get("response") or [])
            )

        merged_rows = merge_fixture_rows(
            previous_live_rows, fresh_rows, now=now, max_age_hours=max_age_hours
        )
        # Exposto para quem chama (cloud_run/futebol/extract_fixtures/main.py): o gate do
        # workflow (saved_count>0) precisa saber quantos fixtures foram ATUALIZADOS neste
        # ciclo, não o total retido no arquivo (que inclui FT antigos mantidos pelo merge).
        self.last_fresh_count = len(fresh_rows)
        logger.info(
            f"live: {len(fresh_rows)} recém-buscados "
            f"({len(candidate_ids) - len(already_fresh_ids & set(candidate_ids))} via ?ids=), "
            f"{len(merged_rows)} linhas após merge/poda"
        )
        return {"mode": "live", "total_fixtures": len(merged_rows), "fixtures": merged_rows}

    def extract_and_save(self, **kwargs) -> str:
        """Override: usa mode no path do GCS em vez de date.

        fixtures é a TABELA MÃE (alimenta odds/predictions/stats/lineups, forward-only).
        Se algum target obrigatório FALHOU (errors no envelope), NÃO sobrescreve o
        snapshot bom anterior por um parcial — aborta com raise (o workflow marca
        PARTIAL_FAILURE e o arquivo antigo é preservado). 'Liga sem dados' (response
        vazio sem errors) é legítimo e não impede o save.

        Modo "live" (DE#60) segue caminho próprio — ver extract_live().
        """
        if self.mode == "live":
            logger.info("Iniciando extração fixtures (mode=live)")
            data = self.extract_live(**kwargs)
            gcs_path = self.storage.upload_json(
                data=data,
                endpoint="fixtures",
                season=0,
                sport="futebol",
                mode="live",
            )
            logger.info(f"Extração live concluída: {gcs_path} ({data['total_fixtures']} linhas)")
            return gcs_path

        logger.info(f"Iniciando extração fixtures (mode={self.mode})")
        data = self.extract(**kwargs)

        failed_targets = data.get("failed_targets") or []
        if failed_targets:
            raise RuntimeError(
                f"fixtures (mode={self.mode}): {len(failed_targets)} target(s) falharam "
                f"({failed_targets}). Abortando p/ NÃO sobrescrever o arquivo bom no GCS "
                "com coleta parcial (fixtures é tabela mãe). Re-executar."
            )

        if data.get("total_fixtures", 0) == 0:
            logger.warning("Nenhuma fixture coletada — arquivo será uploadado vazio (metadata only)")

        data.pop("failed_targets", None)  # controle interno, fora do payload
        gcs_path = self.storage.upload_json(
            data=data,
            endpoint="fixtures",
            season=0,  # ignorado pelo branch sport='futebol' do get_gcs_path
            sport="futebol",
            mode=self.mode,
        )
        logger.info(f"Extração concluída: {gcs_path}")
        return gcs_path
