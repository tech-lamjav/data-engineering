"""Cria dataset 'futebol' e external tables sobre GCS NDJSON (API-Football).

Sibling do `create_bigquery_external_tables.py` (NBA), separado para não
poluir as iterações sobre ENDPOINT_CONFIGS específicas de NBA.
"""
import sys
from pathlib import Path

# scripts/futebol/create_external_tables.py → sobe 3 níveis até data-engineering/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from google.cloud import bigquery
from src.bigquery.bigquery_client import BigQueryClient
from src.config import (
    GCS_BUCKET_NAME,
    BIGQUERY_PROJECT_ID,
    BIGQUERY_DATASET_FUTEBOL,
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        logger.info(
            f"Criando external tables em {BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET_FUTEBOL}"
        )
        bq = BigQueryClient(
            project_id=BIGQUERY_PROJECT_ID,
            dataset_id=BIGQUERY_DATASET_FUTEBOL,
        )

        bq.create_dataset_if_not_exists(
            description="External tables sobre dados brutos da API-Football v3 (soccer)",
        )

        # raw_futebol_leagues — wildcard cobre tanto _current.json quanto _backfill.json
        leagues_uri = f"gs://{GCS_BUCKET_NAME}/futebol/leagues/*.json"
        bq.create_external_table(
            table_id="raw_futebol_leagues",
            uri=leagues_uri,
            description=(
                "API-Football /leagues, NDJSON. 1 linha por (league_id, season) requisitada. "
                "Arquivos: raw_futebol_leagues_current.json (ano corrente, diário) + "
                "raw_futebol_leagues_backfill.json (anos anteriores, one-shot)."
            ),
        )
        logger.info(f"✓ raw_futebol_leagues criada (URI: {leagues_uri})")

        # raw_futebol_teams — mesma estrutura: wildcard sobre current+backfill
        teams_uri = f"gs://{GCS_BUCKET_NAME}/futebol/teams/*.json"
        bq.create_external_table(
            table_id="raw_futebol_teams",
            uri=teams_uri,
            description=(
                "API-Football /teams, NDJSON. 1 linha por (team, league, season) requisitada. "
                "Arquivos: raw_futebol_teams_current.json (ano corrente, diário) + "
                "raw_futebol_teams_backfill.json (anos anteriores, one-shot)."
            ),
        )
        logger.info(f"✓ raw_futebol_teams criada (URI: {teams_uri})")

        # raw_futebol_players — wildcard sobre current+backfill, 1 linha por jogador requisitado
        players_uri = f"gs://{GCS_BUCKET_NAME}/futebol/players/*.json"
        bq.create_external_table(
            table_id="raw_futebol_players",
            uri=players_uri,
            description=(
                "API-Football /players, NDJSON. 1 linha por (player, league, season) requisitada. "
                "Mesmo jogador pode aparecer em múltiplas (liga, temporada) — dedup em dim_players. "
                "Arquivos: raw_futebol_players_current.json (ano corrente, diário) + "
                "raw_futebol_players_backfill.json (anos anteriores, one-shot)."
            ),
        )
        logger.info(f"✓ raw_futebol_players criada (URI: {players_uri})")

        # raw_futebol_fixtures — tabela mãe; wildcard sobre current+backfill, 1 linha por jogo
        fixtures_uri = f"gs://{GCS_BUCKET_NAME}/futebol/fixtures/*.json"
        bq.create_external_table(
            table_id="raw_futebol_fixtures",
            uri=fixtures_uri,
            description=(
                "API-Football /fixtures, NDJSON. 1 linha por fixture (jogo) requisitada. "
                "Espinha do pipeline — fixture_id destrava stats/events/lineups/player_stats. "
                "Arquivos: raw_futebol_fixtures_current.json (ano corrente, diário) + "
                "raw_futebol_fixtures_backfill.json (anos anteriores, one-shot)."
            ),
        )
        logger.info(f"✓ raw_futebol_fixtures criada (URI: {fixtures_uri})")

        # raw_futebol_fixture_statistics — 1 arquivo por fixture (2 linhas: mandante/visitante);
        # wildcard cobre todos os jogos. statistics fica como array aninhado (pivot em stg).
        # Schema EXPLÍCITO (não autodetect): o autodetect inferia statistics.value como FLOAT
        # (coerção de strings numéricas tipo "6"), o que perdia "58%"/"88%" (Ball Possession /
        # Passes %) e quebrava o REPLACE no stg. value é sempre STRING (stringificado no extractor).
        fixture_stats_schema = [
            bigquery.SchemaField("fixture_id", "INTEGER"),
            bigquery.SchemaField("loaded_at", "TIMESTAMP"),
            bigquery.SchemaField("total_teams", "INTEGER"),
            bigquery.SchemaField("team", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("logo", "STRING"),
            ]),
            bigquery.SchemaField("statistics", "RECORD", mode="REPEATED", fields=[
                bigquery.SchemaField("type", "STRING"),
                bigquery.SchemaField("value", "STRING"),
            ]),
        ]
        fixture_stats_uri = f"gs://{GCS_BUCKET_NAME}/futebol/fixture_statistics/*.json"
        bq.create_external_table(
            table_id="raw_futebol_fixture_statistics",
            uri=fixture_stats_uri,
            schema=fixture_stats_schema,
            description=(
                "API-Football /fixtures/statistics, NDJSON. 2 linhas por fixture "
                "(mandante e visitante), 1 arquivo por fixture_id. statistics fica como "
                "array aninhado [{type STRING, value STRING}] (value stringificado; schema "
                "explícito p/ não virar FLOAT) — pivot em stg_futebol_fixture_statistics. "
                "Só jogos finalizados (FT/AET/PEN)."
            ),
        )
        logger.info(f"✓ raw_futebol_fixture_statistics criada (URI: {fixture_stats_uri})")

        # raw_futebol_fixture_events — 1 arquivo por fixture (N linhas: 1 por evento);
        # wildcard cobre todos os jogos. event_order = índice do evento no array da API
        # (preserva a ordem cronológica). team/player/assist ficam aninhados (achatados
        # em stg_futebol_fixture_events). Schema EXPLÍCITO (não autodetect): extra/assist/
        # comments são quase sempre null e o autodetect erraria/abortaria o tipo dessas colunas.
        fixture_events_schema = [
            bigquery.SchemaField("total_events", "INTEGER"),
            bigquery.SchemaField("fixture_id", "INTEGER"),
            bigquery.SchemaField("loaded_at", "TIMESTAMP"),
            bigquery.SchemaField("event_order", "INTEGER"),
            bigquery.SchemaField("elapsed", "INTEGER"),
            bigquery.SchemaField("extra", "INTEGER"),
            bigquery.SchemaField("team", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("logo", "STRING"),
            ]),
            bigquery.SchemaField("player", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
            ]),
            bigquery.SchemaField("assist", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
            ]),
            bigquery.SchemaField("type", "STRING"),
            bigquery.SchemaField("detail", "STRING"),
            bigquery.SchemaField("comments", "STRING"),
        ]
        fixture_events_uri = f"gs://{GCS_BUCKET_NAME}/futebol/fixture_events/*.json"
        bq.create_external_table(
            table_id="raw_futebol_fixture_events",
            uri=fixture_events_uri,
            schema=fixture_events_schema,
            description=(
                "API-Football /fixtures/events, NDJSON. N linhas por fixture (1 por "
                "evento: gol/cartão/subst/VAR), 1 arquivo por fixture_id. event_order "
                "carimba a ordem cronológica (a API não traz id/ordem). team/player/assist "
                "aninhados — achatados em stg_futebol_fixture_events. Só jogos finalizados "
                "(FT/AET/PEN)."
            ),
        )
        logger.info(f"✓ raw_futebol_fixture_events criada (URI: {fixture_events_uri})")

        # raw_futebol_fixture_lineups — 1 arquivo por (fixture, fase); 2 linhas por arquivo
        # (mandante/visitante). Wildcard cobre _confirmed (pré-jogo ~T-30min) + _real
        # (pós-jogo). startXI/substitutes ficam aninhados (UNNEST em stg). Schema EXPLÍCITO
        # (não autodetect): team traz subobjeto colors variável e grid/coach.photo/number
        # vêm nulos em muitos jogos — autodetect erraria/abortaria esses tipos. lineup_phase
        # distingue a fase; o fato é latest-wins (dedup por loaded_at no dbt).
        player_fields = [
            bigquery.SchemaField("player", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("number", "INTEGER"),
                bigquery.SchemaField("pos", "STRING"),
                bigquery.SchemaField("grid", "STRING"),
            ]),
        ]
        fixture_lineups_schema = [
            bigquery.SchemaField("fixture_id", "INTEGER"),
            bigquery.SchemaField("loaded_at", "TIMESTAMP"),
            bigquery.SchemaField("total_teams", "INTEGER"),
            bigquery.SchemaField("lineup_phase", "STRING"),  # "confirmed" | "real"
            bigquery.SchemaField("formation", "STRING"),
            bigquery.SchemaField("team", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("logo", "STRING"),
            ]),
            bigquery.SchemaField("coach", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("photo", "STRING"),
            ]),
            bigquery.SchemaField("startXI", "RECORD", mode="REPEATED", fields=player_fields),
            bigquery.SchemaField("substitutes", "RECORD", mode="REPEATED", fields=player_fields),
        ]
        fixture_lineups_uri = f"gs://{GCS_BUCKET_NAME}/futebol/fixture_lineups/*.json"
        bq.create_external_table(
            table_id="raw_futebol_fixture_lineups",
            uri=fixture_lineups_uri,
            schema=fixture_lineups_schema,
            description=(
                "API-Football /fixtures/lineups, NDJSON. 2 linhas por (fixture, fase) "
                "(mandante e visitante), 1 arquivo por (fixture_id, fase). Wildcard cobre "
                "_confirmed (pré-jogo ~T-30min) + _real (pós-jogo). team/coach + startXI/"
                "substitutes aninhados — flatten/UNNEST em stg_futebol_fixture_lineups(_players). "
                "lineup_phase = 'confirmed'|'real'; fato é latest-wins (dedup por loaded_at)."
            ),
        )
        logger.info(f"✓ raw_futebol_fixture_lineups criada (URI: {fixture_lineups_uri})")

        # raw_futebol_fixture_player_stats — 1 arquivo por fixture (N linhas: 1 por
        # jogador que entrou em campo, ambos os times; ~22-30/jogo). Wildcard cobre
        # todos os jogos. team/player + statistics ficam aninhados (flatten em stg).
        # Schema EXPLÍCITO (não autodetect): a MAIORIA dos campos aninhados vem null em
        # muitos jogos (offsides/shots/tackles/penalty...) e games.rating + passes.accuracy
        # são STRING — autodetect inferiria/abortaria os tipos de forma divergente ao longo
        # do wildcard. (Não é o caso "%" do statistics: aqui statistics NÃO é {type,value}.)
        # ⚠️ penalty.commited tem a grafia ERRADA da própria API (1 "t") — a chave precisa
        # bater com o JSON; o alias correto (penalty_committed) é dado no stg. Já fouls.committed
        # vem grafado certo (2 "t"). Só jogos finalizados (FT/AET/PEN).
        fixture_player_stats_schema = [
            bigquery.SchemaField("fixture_id", "INTEGER"),
            bigquery.SchemaField("loaded_at", "TIMESTAMP"),
            bigquery.SchemaField("total_players", "INTEGER"),
            bigquery.SchemaField("team", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("logo", "STRING"),
            ]),
            bigquery.SchemaField("player", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("photo", "STRING"),
            ]),
            bigquery.SchemaField("statistics", "RECORD", fields=[  # struct único (statistics[0])
                bigquery.SchemaField("games", "RECORD", fields=[
                    bigquery.SchemaField("minutes", "INTEGER"),
                    bigquery.SchemaField("number", "INTEGER"),
                    bigquery.SchemaField("position", "STRING"),
                    bigquery.SchemaField("rating", "STRING"),       # string "6.3" — cast no stg
                    bigquery.SchemaField("captain", "BOOLEAN"),
                    bigquery.SchemaField("substitute", "BOOLEAN"),
                ]),
                bigquery.SchemaField("offsides", "INTEGER"),
                bigquery.SchemaField("shots", "RECORD", fields=[
                    bigquery.SchemaField("total", "INTEGER"),
                    bigquery.SchemaField("on", "INTEGER"),
                ]),
                bigquery.SchemaField("goals", "RECORD", fields=[
                    bigquery.SchemaField("total", "INTEGER"),
                    bigquery.SchemaField("conceded", "INTEGER"),
                    bigquery.SchemaField("assists", "INTEGER"),
                    bigquery.SchemaField("saves", "INTEGER"),
                ]),
                bigquery.SchemaField("passes", "RECORD", fields=[
                    bigquery.SchemaField("total", "INTEGER"),
                    bigquery.SchemaField("key", "INTEGER"),
                    bigquery.SchemaField("accuracy", "STRING"),     # string — cast no stg
                ]),
                bigquery.SchemaField("tackles", "RECORD", fields=[
                    bigquery.SchemaField("total", "INTEGER"),
                    bigquery.SchemaField("blocks", "INTEGER"),
                    bigquery.SchemaField("interceptions", "INTEGER"),
                ]),
                bigquery.SchemaField("duels", "RECORD", fields=[
                    bigquery.SchemaField("total", "INTEGER"),
                    bigquery.SchemaField("won", "INTEGER"),
                ]),
                bigquery.SchemaField("dribbles", "RECORD", fields=[
                    bigquery.SchemaField("attempts", "INTEGER"),
                    bigquery.SchemaField("success", "INTEGER"),
                    bigquery.SchemaField("past", "INTEGER"),
                ]),
                bigquery.SchemaField("fouls", "RECORD", fields=[
                    bigquery.SchemaField("drawn", "INTEGER"),
                    bigquery.SchemaField("committed", "INTEGER"),   # grafia certa (2 "t")
                ]),
                bigquery.SchemaField("cards", "RECORD", fields=[
                    bigquery.SchemaField("yellow", "INTEGER"),
                    bigquery.SchemaField("red", "INTEGER"),
                ]),
                bigquery.SchemaField("penalty", "RECORD", fields=[
                    bigquery.SchemaField("won", "INTEGER"),
                    bigquery.SchemaField("commited", "INTEGER"),    # SIC — grafia errada da API
                    bigquery.SchemaField("scored", "INTEGER"),
                    bigquery.SchemaField("missed", "INTEGER"),
                    bigquery.SchemaField("saved", "INTEGER"),
                ]),
            ]),
        ]
        fixture_player_stats_uri = f"gs://{GCS_BUCKET_NAME}/futebol/fixture_player_stats/*.json"
        bq.create_external_table(
            table_id="raw_futebol_fixture_player_stats",
            uri=fixture_player_stats_uri,
            schema=fixture_player_stats_schema,
            description=(
                "API-Football /fixtures/players, NDJSON. N linhas por fixture (1 por jogador "
                "que entrou em campo, ambos os times; ~22-30/jogo), 1 arquivo por fixture_id. "
                "team/player + statistics aninhados (statistics é struct único — flatten em "
                "stg_futebol_fixture_player_stats). Schema explícito (campos null-heavy + "
                "rating/accuracy string; penalty.commited com grafia errada da API). "
                "Só jogos finalizados (FT/AET/PEN)."
            ),
        )
        logger.info(f"✓ raw_futebol_fixture_player_stats criada (URI: {fixture_player_stats_uri})")

        # raw_futebol_team_season_stats — 1 arquivo por mode (latest-only); N linhas
        # (1 por time × liga × season). Wildcard cobre current+backfill. O extractor curou
        # o objeto único do /teams/statistics num subconjunto aninhado pro Poisson (descartou
        # os buckets por minuto/under_over/cards/lineups). Schema EXPLÍCITO (não autodetect):
        # goals.*.average e penalty.*.percentage vêm STRING ("1.5", "100%") — autodetect
        # inferiria FLOAT e perderia o "%"; demais campos podem vir null em ligas sem stats.
        hat_int = [
            bigquery.SchemaField("home", "INTEGER"),
            bigquery.SchemaField("away", "INTEGER"),
            bigquery.SchemaField("total", "INTEGER"),
        ]
        hat_str = [
            bigquery.SchemaField("home", "STRING"),
            bigquery.SchemaField("away", "STRING"),
            bigquery.SchemaField("total", "STRING"),
        ]
        home_away_str = [
            bigquery.SchemaField("home", "STRING"),
            bigquery.SchemaField("away", "STRING"),
        ]
        home_away_int = [
            bigquery.SchemaField("home", "INTEGER"),
            bigquery.SchemaField("away", "INTEGER"),
        ]
        team_season_stats_schema = [
            bigquery.SchemaField("requested_league_id", "INTEGER"),
            bigquery.SchemaField("requested_season", "INTEGER"),
            bigquery.SchemaField("snapshot_date", "DATE"),  # data da coleta (chave de partição no fato)
            bigquery.SchemaField("loaded_at", "TIMESTAMP"),
            bigquery.SchemaField("total_teams", "INTEGER"),
            bigquery.SchemaField("team", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("logo", "STRING"),
            ]),
            bigquery.SchemaField("form", "STRING"),
            bigquery.SchemaField("fixtures", "RECORD", fields=[
                bigquery.SchemaField("played", "RECORD", fields=hat_int),
                bigquery.SchemaField("wins", "RECORD", fields=hat_int),
                bigquery.SchemaField("draws", "RECORD", fields=hat_int),
                bigquery.SchemaField("loses", "RECORD", fields=hat_int),
            ]),
            bigquery.SchemaField("goals", "RECORD", fields=[
                bigquery.SchemaField("for", "RECORD", fields=[
                    bigquery.SchemaField("total", "RECORD", fields=hat_int),
                    bigquery.SchemaField("average", "RECORD", fields=hat_str),  # "1.5" — cast no stg
                ]),
                bigquery.SchemaField("against", "RECORD", fields=[
                    bigquery.SchemaField("total", "RECORD", fields=hat_int),
                    bigquery.SchemaField("average", "RECORD", fields=hat_str),
                ]),
            ]),
            bigquery.SchemaField("clean_sheet", "RECORD", fields=hat_int),
            bigquery.SchemaField("failed_to_score", "RECORD", fields=hat_int),
            bigquery.SchemaField("biggest", "RECORD", fields=[
                bigquery.SchemaField("streak", "RECORD", fields=[
                    bigquery.SchemaField("wins", "INTEGER"),
                    bigquery.SchemaField("draws", "INTEGER"),
                    bigquery.SchemaField("loses", "INTEGER"),
                ]),
                bigquery.SchemaField("wins", "RECORD", fields=home_away_str),   # placares "3-0"
                bigquery.SchemaField("loses", "RECORD", fields=home_away_str),
                bigquery.SchemaField("goals", "RECORD", fields=[
                    bigquery.SchemaField("for", "RECORD", fields=home_away_int),
                    bigquery.SchemaField("against", "RECORD", fields=home_away_int),
                ]),
            ]),
            bigquery.SchemaField("penalty", "RECORD", fields=[
                bigquery.SchemaField("scored", "RECORD", fields=[
                    bigquery.SchemaField("total", "INTEGER"),
                    bigquery.SchemaField("percentage", "STRING"),  # "100%" — cast no stg
                ]),
                bigquery.SchemaField("missed", "RECORD", fields=[
                    bigquery.SchemaField("total", "INTEGER"),
                    bigquery.SchemaField("percentage", "STRING"),
                ]),
                bigquery.SchemaField("total", "INTEGER"),
            ]),
        ]
        team_season_stats_uri = f"gs://{GCS_BUCKET_NAME}/futebol/team_season_stats/*.json"
        bq.create_external_table(
            table_id="raw_futebol_team_season_stats",
            uri=team_season_stats_uri,
            schema=team_season_stats_schema,
            description=(
                "API-Football /teams/statistics, NDJSON. 1 linha por (time, liga, season), "
                "1 arquivo por mode (latest-only): raw_futebol_team_season_stats_current.json "
                "(Brasileirão 2026 + Copa 2026, semanal) + _backfill.json (Brasileirão 2024/2025, "
                "one-shot). Objeto único da API curado em forma aninhada (fixtures/goals/clean_sheet/"
                "failed_to_score/biggest/penalty; buckets por minuto/under_over/cards/lineups "
                "descartados) — achatado em stg_futebol_team_season_stats. snapshot_date = data da "
                "coleta (chave de partição). Schema explícito (averages/percentage STRING)."
            ),
        )
        logger.info(f"✓ raw_futebol_team_season_stats criada (URI: {team_season_stats_uri})")

        # raw_futebol_standings — snapshot DIÁRIO da tabela do campeonato. Diferente
        # dos demais (latest-only), 1 arquivo por dia+mode (date-stampado:
        # raw_futebol_standings_{mode}_{YYYY-MM-DD}.json) — o GCS acumula o histórico
        # de evolução e o wildcard cobre tudo. N linhas por arquivo (1 por time × liga
        # × season; grupos da Copa já achatados no extractor). Schema EXPLÍCITO:
        # all/group/goals.for são keywords SQL (crases no stg) e description/form
        # podem vir null — autodetect seria frágil.
        standings_record = [  # shape de all/home/away
            bigquery.SchemaField("played", "INTEGER"),
            bigquery.SchemaField("win", "INTEGER"),
            bigquery.SchemaField("draw", "INTEGER"),
            bigquery.SchemaField("lose", "INTEGER"),
            bigquery.SchemaField("goals", "RECORD", fields=[
                bigquery.SchemaField("for", "INTEGER"),
                bigquery.SchemaField("against", "INTEGER"),
            ]),
        ]
        standings_schema = [
            bigquery.SchemaField("requested_league_id", "INTEGER"),
            bigquery.SchemaField("requested_season", "INTEGER"),
            bigquery.SchemaField("snapshot_date", "DATE"),  # data da coleta (chave de partição no fato)
            bigquery.SchemaField("loaded_at", "TIMESTAMP"),
            bigquery.SchemaField("total_rows", "INTEGER"),
            bigquery.SchemaField("rank", "INTEGER"),
            bigquery.SchemaField("team", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("logo", "STRING"),
            ]),
            bigquery.SchemaField("points", "INTEGER"),
            bigquery.SchemaField("goalsDiff", "INTEGER"),
            bigquery.SchemaField("group", "STRING"),       # "Serie A" | "Group A"...
            bigquery.SchemaField("form", "STRING"),        # últimos 5 jogos ("WWDLW")
            bigquery.SchemaField("status", "STRING"),      # "same" | "up" | "down"
            bigquery.SchemaField("description", "STRING"), # "Promotion - Libertadores" | null
            bigquery.SchemaField("all", "RECORD", fields=standings_record),
            bigquery.SchemaField("home", "RECORD", fields=standings_record),
            bigquery.SchemaField("away", "RECORD", fields=standings_record),
            bigquery.SchemaField("update", "TIMESTAMP"),   # última atualização da tabela na API
        ]
        standings_uri = f"gs://{GCS_BUCKET_NAME}/futebol/standings/*.json"
        bq.create_external_table(
            table_id="raw_futebol_standings",
            uri=standings_uri,
            schema=standings_schema,
            description=(
                "API-Football /standings, NDJSON. Snapshot diário da tabela do campeonato: "
                "N linhas por (liga, season, snapshot_date) — 1 por time×grupo (na Copa o "
                "time aparece no grupo e no 'Ranking of third-placed teams'). 1 arquivo por "
                "dia+mode (date-stampado, acumula histórico; re-run no mesmo dia sobrescreve "
                "= idempotente): _current_{data}.json (Brasileirão 2026 + Copa 2026, diário) "
                "+ _backfill_{data}.json (tabela FINAL Brasileirão 2024/2025, one-shot). "
                "snapshot_date = data da coleta (partição no fato); `update` = última "
                "atualização da tabela na API. Grupos da Copa achatados (coluna group). "
                "Schema explícito (all/group/goals.for são keywords SQL — crases no stg)."
            ),
        )
        logger.info(f"✓ raw_futebol_standings criada (URI: {standings_uri})")

        # raw_futebol_injuries — snapshot DIÁRIO de lesionados/suspensos. Igual ao
        # standings (e diferente dos latest-only), 1 arquivo por dia+mode (date-stampado:
        # raw_futebol_injuries_{mode}_{YYYY-MM-DD}.json) — o GCS acumula o histórico e o
        # wildcard cobre tudo. N linhas/arquivo (1 por player×fixture×type×reason — a API
        # repete linhas EXATAS; dedup no fato). Schema EXPLÍCITO: ⚠️ type/reason vêm
        # ANINHADOS em player (não no topo!) e devem ser STRING; structs aninhados podem
        # vir null ao longo do wildcard — autodetect seria frágil. fixture.date é ISO 8601 → TIMESTAMP
        # (precedente: `update` no standings).
        # ⚠️ Coverage: só Brasileirão (71) retorna dados; Copa do Mundo (1) tem
        # coverage.injuries=FALSE (validado em dim_leagues) e está fora dos targets.
        injuries_schema = [
            bigquery.SchemaField("requested_league_id", "INTEGER"),
            bigquery.SchemaField("requested_season", "INTEGER"),
            bigquery.SchemaField("snapshot_date", "DATE"),  # data da coleta (chave de partição no fato)
            bigquery.SchemaField("loaded_at", "TIMESTAMP"),
            bigquery.SchemaField("total_rows", "INTEGER"),
            bigquery.SchemaField("player", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("photo", "STRING"),
                bigquery.SchemaField("type", "STRING"),    # "Missing Fixture" | "Questionable" — ⚠️ aninhado em player
                bigquery.SchemaField("reason", "STRING"),  # texto livre (inclui suspensões) — ⚠️ aninhado em player
            ]),
            bigquery.SchemaField("team", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("logo", "STRING"),
            ]),
            bigquery.SchemaField("fixture", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("timezone", "STRING"),
                bigquery.SchemaField("date", "TIMESTAMP"),    # ISO 8601 com offset
                bigquery.SchemaField("timestamp", "INTEGER"),
            ]),
            bigquery.SchemaField("league", "RECORD", fields=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("season", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("country", "STRING"),
                bigquery.SchemaField("logo", "STRING"),
                bigquery.SchemaField("flag", "STRING"),
            ]),
        ]
        injuries_uri = f"gs://{GCS_BUCKET_NAME}/futebol/injuries/*.json"
        bq.create_external_table(
            table_id="raw_futebol_injuries",
            uri=injuries_uri,
            schema=injuries_schema,
            description=(
                "API-Football /injuries, NDJSON. Snapshot diário de lesionados/suspensos: "
                "N linhas por (liga, season, snapshot_date) — 1 por (player, fixture, type, "
                "reason); a API repete linhas exatas (dedup no fato). 1 arquivo "
                "por dia+mode (date-stampado, acumula histórico; re-run no mesmo dia sobrescreve "
                "= idempotente): _current_{data}.json (Brasileirão 2026, diário) + "
                "_backfill_{data}.json (Brasileirão 2024/2025, one-shot). snapshot_date = data "
                "da coleta (partição no fato). type ∈ {Missing Fixture, Questionable}; reason é "
                "texto livre (inclui suspensões). Schema explícito (type/reason STRING aninhados "
                "em player; fixture.date ISO→TIMESTAMP). ⚠️ Só Brasileirão tem coverage.injuries=TRUE "
                "(Copa do Mundo fora — coverage FALSE)."
            ),
        )
        logger.info(f"✓ raw_futebol_injuries criada (URI: {injuries_uri})")

        # raw_futebol_odds — snapshot pré-jogo de odds (value betting). FORWARD-ONLY, 2
        # janelas por jogo (collection_window t24h|t1h). 1 arquivo por (fixture, janela):
        # raw_futebol_odds_{fixture}_{t24h|t1h}.json — o wildcard cobre todos. N linhas por
        # arquivo (1 por CASA), bets/values ANINHADOS (UNNEST no dbt). Schema EXPLÍCITO: a
        # odd vem como STRING decimal ("1.85") — autodetect viraria FLOAT e perderia precisão/
        # mistura de tipos; cast p/ FLOAT64 só no stg. Guarda TODAS as casas/mercados; o
        # afunilamento p/ os 8 mercados-alvo é no fact_odds_snapshot. collection_timestamp e
        # api_update são ISO → TIMESTAMP; kickoff_timestamp é unix INTEGER (TIMESTAMP_SECONDS no dbt).
        odds_value_record = [
            bigquery.SchemaField("value", "STRING"),  # rótulo do outcome ("Home", "Over 2.5", "1.5"...)
            bigquery.SchemaField("odd", "STRING"),     # odd decimal como STRING — cast p/ FLOAT64 no stg
        ]
        odds_bet_record = [
            bigquery.SchemaField("id", "INTEGER"),     # market_id (1=Match Winner, 5=Goals O/U, 8=BTTS...)
            bigquery.SchemaField("name", "STRING"),
            bigquery.SchemaField("values", "RECORD", mode="REPEATED", fields=odds_value_record),
        ]
        odds_schema = [
            bigquery.SchemaField("fixture_id", "INTEGER"),
            bigquery.SchemaField("league_id", "INTEGER"),
            bigquery.SchemaField("season", "INTEGER"),
            bigquery.SchemaField("collection_window", "STRING"),       # "t24h" | "t1h"
            bigquery.SchemaField("collection_timestamp", "TIMESTAMP"), # momento da coleta (UTC) — partição no fato
            bigquery.SchemaField("kickoff_timestamp", "INTEGER"),      # unix UTC do kickoff
            bigquery.SchemaField("api_update", "TIMESTAMP"),           # última atualização das odds na API (ISO)
            bigquery.SchemaField("loaded_at", "TIMESTAMP"),
            bigquery.SchemaField("total_bookmakers", "INTEGER"),
            bigquery.SchemaField("bookmaker_id", "INTEGER"),
            bigquery.SchemaField("bookmaker_name", "STRING"),
            bigquery.SchemaField("bets", "RECORD", mode="REPEATED", fields=odds_bet_record),
        ]
        odds_uri = f"gs://{GCS_BUCKET_NAME}/futebol/odds/*.json"
        bq.create_external_table(
            table_id="raw_futebol_odds",
            uri=odds_uri,
            schema=odds_schema,
            description=(
                "API-Football /odds, NDJSON. Snapshot pré-jogo de odds (CLV/EV/value betting), "
                "FORWARD-ONLY, em 2 janelas por jogo (collection_window t24h = linha de abertura, "
                "t1h = perto do fechamento). N linhas por (fixture, janela) — 1 por CASA; "
                "bets/values aninhados (REPEATED). 1 arquivo por (fixture, janela): "
                "raw_futebol_odds_{fixture}_{t24h|t1h}.json (skip-if-exists = 1 snapshot/janela). "
                "Guarda TODAS as casas/mercados; o fact afunila p/ os 8 mercados-alvo. Schema "
                "explícito (odd/value STRING — cast no stg; collection_timestamp/api_update ISO→"
                "TIMESTAMP; kickoff_timestamp unix INTEGER). Brasileirão (71) + Copa do Mundo (1) "
                "2026 (ambos coverage.odds=TRUE)."
            ),
        )
        logger.info(f"✓ raw_futebol_odds criada (URI: {odds_uri})")

        return 0
    except Exception as e:
        logger.error(f"✗ Erro: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
