"""Configurações centralizadas do projeto."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis de ambiente do arquivo .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# API Configuration
API_BASE_URL = "https://api.balldontlie.io/v1"
API_BASE_URL_V2 = "https://api.balldontlie.io/v2"  # Para endpoints v2
API_BASE_URL_NBA = "https://api.balldontlie.io/nba/v1"  # Para team_season_averages
API_BASE_URL_NBA_V2 = "https://api.balldontlie.io/nba/v2"  # Para stats/advanced game-by-game
API_KEY = os.getenv("BALLDONTLIE_KEY")
API_TIMEOUT = 60

# API-Football (futebol/soccer) — vertical paralela à NBA
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

# Ligas alvo do pipeline futebol
BRASILEIRAO_ID = 71
COPA_MUNDO_ID = 1  # validar no primeiro run
SERIE_B_ID = 72  # odds/predictions TRUE, injuries FALSE (coverage validado 2026-07)
COPA_DO_BRASIL_ID = 73  # mata-mata: standings/injuries FALSE; predictions TRUE; odds sazonal -> FASE 2 (validado 2026-07-10)
LIBERTADORES_ID = 13  # grupos+mata-mata: standings/predictions TRUE; injuries FALSE em 2026 (2025 foi TRUE — rechecar em ago); odds armada dia 0, dormente até 10/08 (validado 2026-07-13)
SUDAMERICANA_ID = 11  # par da Libertadores (grupos+mata-mata): standings/predictions TRUE; injuries FALSE em 2024/25/26 (exclusão simples, sem o caveat 2025 da Liberta); odds armada dia 0, dormente até t24h ~20/07 (R32 21–31/07; oitavas ida 11–13/08, volta 18–20/08) (validado 2026-07-14)

# Split entre backfill (one-shot, anos anteriores) e current (diário, ano corrente)
LEAGUES_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (COPA_DO_BRASIL_ID, 2024),
    (COPA_DO_BRASIL_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
]
LEAGUES_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (COPA_DO_BRASIL_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
]

# Idem leagues — 4 chamadas distribuídas entre backfill (one-shot) e current (diário).
TEAMS_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (COPA_DO_BRASIL_ID, 2024),
    (COPA_DO_BRASIL_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
]
TEAMS_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (COPA_DO_BRASIL_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
]

# Idem teams — catálogo de jogadores via /players?league=&season= (paginado).
PLAYERS_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (COPA_DO_BRASIL_ID, 2024),
    (COPA_DO_BRASIL_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
]
PLAYERS_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (COPA_DO_BRASIL_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
]

# Fixtures (jogos) — tabela mãe via /fixtures?league=&season= (paginado).
# Cada fixture_id destrava stats/events/lineups/player_stats (subtasks 5-8).
FIXTURES_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (COPA_DO_BRASIL_ID, 2024),
    (COPA_DO_BRASIL_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
]
FIXTURES_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (COPA_DO_BRASIL_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
]

# Standings (/standings) — snapshot diário da tabela do campeonato (1 chamada por
# liga×season, ~20 linhas Brasileirão). Diferente dos demais (latest-only), o arquivo
# é date-stampado (raw_futebol_standings_{mode}_{YYYY-MM-DD}.json): o GCS acumula 1
# snapshot por dia (histórico de evolução da tabela) e re-rodar no mesmo dia
# sobrescreve o mesmo arquivo (idempotente). A API não tem histórico diário — o
# backfill captura a tabela FINAL de 2024/2025 com snapshot_date do dia da coleta.
# ⚠️ Copa do Brasil (73) EXCLUÍDA (backfill E current): mata-mata puro, coverage.standings=FALSE
# (validado 2026-07-10) — o extractor itera SÓ estas tuplas; incluí-la gastaria quota e voltaria vazia.
# Libertadores (13) INCLUÍDA (≠ Copa do Brasil): a fase de grupos tem tabela (coverage.standings=TRUE,
# validado 2026-07-13) e o extractor já achata os N grupos (Group A–H, rank 1-4 por grupo). No
# mata-mata (ago+) a API serve a tabela final dos grupos congelada — snapshot diário segue barato.
# Sudamericana (11) INCLUÍDA (validado 2026-07-14): mesmo formato da 13 — grupos A–H com tabela
# (32 times, rank 1-4/grupo); no mata-mata idem, tabela final dos grupos congelada.
STANDINGS_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
]
STANDINGS_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
]

# Injuries (/injuries) — snapshot diário de lesionados/suspensos (1 chamada/liga×season,
# NÃO paginada — como /fixtures, rejeita `page` e devolve o log de lesões da temporada
# INTEIRA, ~2k linhas/liga). Mesma mecânica date-stampada do standings
# (raw_futebol_injuries_{mode}_{data}.json): o GCS acumula 1 snapshot/dia e re-rodar no
# mesmo dia sobrescreve (idempotente). ⚠️ A API repete linhas EXATAS — dedup no fato.
# ⚠️ Coverage validado em dim_leagues (2026-06-15): coverage.injuries = TRUE só p/
# Brasileirão (71) 2024/25/26. Copa do Mundo (1) 2026 = FALSE → EXCLUÍDA (a API não
# fornece lesões da Copa; incluí-la gastaria quota e voltaria vazia). Série B (72)
# também EXCLUÍDA: coverage.injuries=FALSE em 2024/25/26 (validado 2026-07).
# Copa do Brasil (73) idem: coverage.injuries=FALSE em 2024/25/26 (validado 2026-07-10).
# Libertadores (13): coverage.injuries=FALSE em 2026 (e 2024), mas 2025 FOI TRUE — RECHECAR em
# agosto via dim_leagues antes de decidir; se virar TRUE: 13 em INJURIES_CURRENT +
# FUTEBOL_INJURIES_LEAGUE_IDS + deploy cirúrgico extract-injuries (validado 2026-07-13).
# Sudamericana (11): coverage.injuries=FALSE em 2024/25/26 (validado 2026-07-14) — exclusão
# simples, SEM recheck de agosto (≠ Liberta).
INJURIES_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
]
INJURIES_CURRENT = [
    (BRASILEIRAO_ID, 2026),
]

# Odds (/odds) — coração do value betting. Snapshot pré-jogo de TODAS as casas em 2
# janelas por jogo (T-24h = linha de abertura; T-1h = linha perto do fechamento) p/
# permitir CLV/EV e movimento de linha. Coleta FORWARD-ONLY (não dá pra reconstruir as
# janelas de jogos passados) via poll ~15min (workflow_futebol_odds.yml), espelhando o
# poll pré-jogo das escalações. 1 chamada por (fixture, janela); 1 arquivo por (fixture,
# janela): raw_futebol_odds_{fixture}_{t24h|t1h|t15m}.json (sufixo de fase no get_gcs_path).
#
# FUTEBOL_ODDS_WINDOWS: banda (lead_min, lead_max) em MINUTOS até o kickoff. A 1ª passada
# do poll com lead na banda captura; skip-if-exists trava as seguintes (1 captura/janela).
# Banda > intervalo de poll p/ não furar; a captura cai perto de lead_max (lead decresce
# no tempo). minutes_to_kickoff exato é registrado no fato — a precisão de CLV não depende
# da largura da banda. t15m (0,15) é a LINHA DE FECHAMENTO (CLV real): banda inclusiva [0,15]
# + poll */15 garante 1 captura perto do kickoff (lead≈15→0); forward-only, cada dia sem
# t15m = linha de fechamento perdida pra sempre.
FUTEBOL_ODDS_WINDOWS = {
    "t24h": (1320, 1440),  # 22h–24h antes (alvo 24h — linha de abertura)
    "t1h":  (30, 60),      # 30–60min antes (alvo 1h — linha intermediária)
    "t15m": (0, 15),       # 0–15min antes (alvo ~15min — linha de fechamento p/ CLV real)
}

# Ligas com coverage.odds=TRUE (validado em dim_leagues). O poll filtra os jogos NS
# por esses league_ids. Diferente de /injuries (Copa excluída), odds de Copa do Mundo
# normalmente existem — manter 1 aqui se a validação confirmar coverage.odds=TRUE.
# Copa do Brasil (73) e Libertadores (13) ARMADAS em 2026-07-13, Sudamericana (11) em 2026-07-14
# (mesma decisão: armar no dia 0, sem deploy futuro p/ não esquecer). Ficam DORMENTES com custo 0
# — o poll só chama /odds p/ NS com lead <=24h — até o t24h abrir: Sudamericana ~20/07
# (R32/repechagem ida 21–24/07, volta 28–31/07; oitavas ida 11–13/08, volta 18–20/08),
# CdB ~31/07 (oitavas 01/08), Libertadores 10/08 (ida 11/08, volta 18/08). coverage.odds é
# sazonal (FALSE fora da janela 1-14d); a checagem de Pinnacle (id=4) virou verificação
# pós-coleta: sem Pinnacle o board não abre 1X2/AH/OU (de-vig ancora nele; consenso só
# BTTS/HT-FT/DC) → feed ruim = board fechado, nunca sinal lixo. ⚠️ RENOVAR O PLANO PRO ATÉ
# 10/08 (expira 11/08 12:21 UTC): sem renovar, t1h/t15m de 11/08, as voltas de 18–20/08 e
# TODO o pipeline diário param — na 11, até o t24h das oitavas de 12–13/08 cai APÓS a expiração.
FUTEBOL_ODDS_LEAGUE_IDS = [BRASILEIRAO_ID, COPA_MUNDO_ID, SERIE_B_ID, COPA_DO_BRASIL_ID, LIBERTADORES_ID, SUDAMERICANA_ID]

# Predictions (/predictions) — BASELINE de comparação (a previsão do algoritmo da própria
# API) E fonte da corroboração `modelo_api_concorda` (+7) do Motor de Score. Não é produto:
# serve p/ avaliar se um modelo nosso bate a API consistentemente (= edge real). FORWARD-ONLY
# (previsão de jogo passado não é reconstruível — a API recomputa com o resultado conhecido),
# poll ~15min dedicado (workflow_futebol_predictions.yml).
#
# DUAS janelas (a API atualiza ~1x/h):
#   - "daily": varre TODO jogo NS de ~2h até 14 dias à frente → garante que jogos futuros
#     tenham previsão (sem isso, `modelo_api_concorda` quase nunca dispara). Recaptura 1x/dia
#     porque o path é date-stampado (raw_futebol_predictions_{fixture}_daily_{YYYY-MM-DD}.json)
#     → skip-if-exists é por (fixture, janela, DIA). A 1ª passada do poll no dia captura; as
#     demais pulam. Bandas DISJUNTAS de t2h (≥131 vs ≤130) p/ não capturar 2x no mesmo poll.
#   - "t2h": refresh perto do kickoff (linha mais fresca p/ o jogo do dia).
# O fato dedup latest-wins por loaded_at → o snapshot mais fresco (t2h no dia, ou o diário
# mais recente) vence; os snapshots acumulam no GCS (padrão de standings/injuries).
#
# FUTEBOL_PREDICTIONS_WINDOWS: banda (lead_min, lead_max) em MINUTOS até o kickoff (mesma
# mecânica de FUTEBOL_ODDS_WINDOWS). Banda > intervalo de poll p/ não furar. Horizonte de 14d
# alinha com a janela de odds (1–14d): a previsão só corrobora quando há odds. Tunável.
FUTEBOL_PREDICTIONS_WINDOWS = {
    "daily": (131, 20160),  # ~2h até 14 dias — varre todo NS futuro; 1 captura/dia (date-stamp)
    "t2h":   (100, 130),    # 100–130min antes (refresh perto do jogo; captura cai perto de 130)
}

# coverage.predictions=TRUE p/ Brasileirão (71) E Copa do Mundo (1) — validado em
# dim_leagues (2026-06-17). Diferente de /injuries (Copa excluída): ambos incluídos.
# Copa do Brasil (73): predictions TRUE e REAIS já nas oitavas (validado 2026-07-10,
# ex.: 35/35/30 + advice — não é o placeholder 45/45/10 de mata-mata da Copa do Mundo).
# Libertadores (13): coverage.predictions=TRUE (validado 2026-07-13). A janela daily de 14d
# só alcança as oitavas ~28/07 (ida 11/08) — 0 linhas até lá é esperado; na 1ª captura
# conferir REAL (padrão CdB) vs placeholder 45/45/10 (padrão Copa do Mundo, viraria ruído
# no modelo_api_concorda).
# Sudamericana (11): coverage.predictions=TRUE (validado 2026-07-14). A R32 (16 NS em 21–31/07)
# JÁ está na janela de 14d → 1ª captura no 1º ciclo de poll pós-deploy; mesma conferência
# REAL vs placeholder da 13 (mata-mata CONMEBOL), só que imediata.
FUTEBOL_PREDICTIONS_LEAGUE_IDS = [BRASILEIRAO_ID, COPA_MUNDO_ID, SERIE_B_ID, COPA_DO_BRASIL_ID, LIBERTADORES_ID, SUDAMERICANA_ID]

# Injuries PRÉ-JOGO (/injuries?fixture) — coleta FORWARD-ONLY por fixture (modo "pregame"),
# complementando o snapshot season-log diário (INJURIES_CURRENT, /injuries?league&season).
# Motivo (S7 do Motor de Score): o season-log fica congelado quando não há rodada recente
# (ex.: pausa FIFA) — não traz desfalques dos JOGOS FUTUROS. O endpoint por fixture devolve
# os lesionados/suspensos ligados àquele jogo específico, então varremos os NS futuros.
#
# Mesma mecânica date-stampada de predictions (raw_futebol_injuries_{fixture}_daily_{data}.json):
# skip-if-exists por (fixture, janela, DIA) → 1 captura/dia; o fato dedup latest-wins por
# loaded_at. Janela ÚNICA "daily" cobre de agora (0) a 14 dias. Por que SÓ "daily" (sem banda
# near-kickoff): injuries são estáveis intra-dia (jogador descartado de manhã segue fora) E o
# fact_injuries_snapshot é daily-grained (dedup por snapshot_date) → re-poll horário não
# adiciona resolução; a notícia final de escalação vem da fonte de lineups (confirmed, ~T-30min).
FUTEBOL_INJURIES_WINDOWS = {
    "daily": (0, 20160),  # 0 até 14 dias (minutos) — varre todo NS futuro; 1 captura/dia
}

# coverage.injuries=TRUE só p/ Brasileirão (71); Copa do Mundo (1) EXCLUÍDA (igual a
# INJURIES_CURRENT — a API não fornece lesões da Copa; incluí-la gastaria quota e voltaria vazia).
# Série B (72) também EXCLUÍDA: coverage.injuries=FALSE (validado 2026-07).
FUTEBOL_INJURIES_LEAGUE_IDS = [BRASILEIRAO_ID]

# Fixture statistics (/fixtures/statistics) — 1 chamada por fixture, só após FT.
# No modo current, re-busca jogos cujo kickoff foi nos últimos N dias (captura
# correções pós-jogo da API); jogos mais antigos já salvos são pulados (skip-if-exists).
FIXTURE_STATS_REFETCH_WINDOW_DAYS = 3

# Fixture events (/fixtures/events) — 1 chamada por fixture, só após FT. Mesma
# mecânica do statistics: no modo current re-busca jogos dos últimos N dias
# (captura VAR/correções pós-jogo da API), o resto é pulado por skip-if-exists.
FIXTURE_EVENTS_REFETCH_WINDOW_DAYS = 3

# Fixture lineups (/fixtures/lineups) — 1 chamada por fixture, em duas fases:
# - pós-jogo (mode current/backfill): escalação real (lineup_phase="real"), mesma
#   mecânica do statistics/events (janela 3d no current + skip-if-exists).
# - pré-jogo (mode pregame): escalação confirmada (lineup_phase="confirmed") dos jogos
#   NS com kickoff nos próximos FIXTURE_LINEUPS_PREGAME_WINDOW_MIN minutos. O fato é
#   latest-wins (dbt) — "real" vence "confirmed"; o GCS guarda os dois snapshots.
FIXTURE_LINEUPS_REFETCH_WINDOW_DAYS = 3
FIXTURE_LINEUPS_PREGAME_WINDOW_MIN = 45

# Fixture player stats (/fixtures/players) — 1 chamada por fixture, só após FT.
# Mesma mecânica do statistics/events: no modo current re-busca jogos dos últimos N
# dias (captura correções pós-jogo da API, ex.: rating revisado), o resto é pulado
# por skip-if-exists.
FIXTURE_PLAYER_STATS_REFETCH_WINDOW_DAYS = 3

# GCS Configuration
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "smartbetting-landing")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCS_USE_ADC = True  # Application Default Credentials

# BigQuery Configuration
BIGQUERY_PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID", "smartbetting-dados")
BIGQUERY_DATASET = os.getenv("BIGQUERY_DATASET", "nba")
BIGQUERY_DATASET_FUTEBOL = os.getenv("BIGQUERY_DATASET_FUTEBOL", "futebol")
BIGQUERY_LOCATION = os.getenv("BIGQUERY_LOCATION", "us-east1")
BIGQUERY_DATASET_SANDBOX = os.getenv("BIGQUERY_DATASET_SANDBOX", "sandbox")

# Supabase Postgres sync configuration
# Connection strings DEVEM usar porta 5432 (sessão direta), NÃO 6543 (pgbouncer):
# pgbouncer em modo transaction não suporta COPY nem prepared statements.
# Dois ambientes: PRD recebe sync agendado via workflow, DEV idem (sequencial).
SUPABASE_PG_URL_PRD = os.getenv("SUPABASE_PG_URL_PRD")
SUPABASE_PG_URL_DEV = os.getenv("SUPABASE_PG_URL_DEV")
MART_PG_SCHEMA = "nba_mart"
# Futebol grava no schema nativo `futebol` (mesmas RPCs get_futebol_* já leem ele).
# Mesmo Supabase PRD/DEV do NBA — só muda dataset BQ de origem e schema destino.
FUTEBOL_MART_PG_SCHEMA = "futebol"


def get_pg_url(env: str) -> str:
    """Resolve URL Postgres por ambiente. Levanta se não configurado."""
    env = (env or "prd").lower()
    if env == "prd":
        url = SUPABASE_PG_URL_PRD
    elif env == "dev":
        url = SUPABASE_PG_URL_DEV
    else:
        raise ValueError(f"env inválido: {env}. Use 'prd' ou 'dev'.")
    if not url:
        raise RuntimeError(
            f"SUPABASE_PG_URL_{env.upper()} não configurado. "
            f"Settar env var (porta 5432, NÃO 6543 pgbouncer)."
        )
    return url

# Ordem deliberada: dimensões primeiro, depois fatos, depois marts derivadas.
# Reduz janela de inconsistência cross-table durante o sync.
# As 6 tabelas de passing / contexto de time / período foram adicionadas em
# 2026-06: tinham ficado de fora do cutover FDW->sync (só 9 dos 15 marts BI
# foram portados). As tabelas destino são criadas pela migration 069 do
# prop-play-predictor (nba_mart). check_schema_parity já validado contra o BQ.
MART_TABLES_ORDERED = [
    # dimensões base
    "dim_teams",
    "dim_players",
    "dim_stat_player",
    "dim_player_shooting_by_zones",
    "dim_player_passing_stats",
    "dim_player_latest_line",
    "dim_team_opponent_stats",
    "dim_team_playtypes",
    "dim_team_shooting_zone_defense",
    # fatos
    "ft_games",
    "ft_game_player_stats",
    "ft_game_player_passing_stats",
    "ft_game_player_stats_period",
    # marts derivadas
    "dim_teammate_impact_360",
    "dim_daily_opportunities",
]

# Futebol — as 21 tabelas que o app (RPCs get_futebol_*) consome, na ordem
# dim -> fact -> intermediário -> mart-produto (mesma lógica de janela mínima de
# inconsistência do NBA). NÃO é só "marts": as RPCs de valor reconstroem
# evidências/avisos a partir dos booleans das int_futebol_premissas_*, então elas
# entram no sync. Espelha o array de futebol.sync_all() que o FDW+pg_cron rodava.
# Colunas BQ complexas (coverage RECORD, evidencias/avisos ARRAY) são puladas pelo
# engine (Postgres nativo é escalar) — ver _is_complex_field em sync/bq_to_postgres.py.
FUTEBOL_SYNC_TABLES_ORDERED = [
    # dimensões
    "dim_leagues",
    "dim_teams",
    # fatos base
    "fact_fixtures",
    "fact_fixture_stats",
    "fact_fixture_events",
    "fact_fixture_lineups",
    "fact_fixture_lineups_players",
    "fact_fixture_player_stats",
    "fact_h2h",
    "fact_injuries_snapshot",
    "fact_standings_snapshot",
    "fact_team_season_stats",
    "fact_odds_snapshot",
    "fact_predictions_api",
    # camada de valor (intermediários -> mart-produto por último)
    "int_futebol_odds_devig",
    "int_futebol_premissas_1x2",
    "int_futebol_premissas_ou",
    "int_futebol_premissas_ah",
    "int_futebol_premissas_btts",
    "int_futebol_premissas_dc",
    "fact_value_opportunities",
]


def get_sync_target(sport: str = "nba") -> tuple:
    """Resolve (dataset BQ, schema Postgres, tabelas ordenadas) por esporte.

    O engine de sync (sync/bq_to_postgres.py) é sport-agnostic; cada esporte amarra
    um dataset BQ a um schema Postgres + a allowlist ordenada (dim -> fact -> mart).
    Mantém o NBA como default → comportamento idêntico ao anterior.
    """
    sport = (sport or "nba").lower()
    if sport == "nba":
        return BIGQUERY_DATASET, MART_PG_SCHEMA, list(MART_TABLES_ORDERED)
    if sport == "futebol":
        return BIGQUERY_DATASET_FUTEBOL, FUTEBOL_MART_PG_SCHEMA, list(FUTEBOL_SYNC_TABLES_ORDERED)
    raise ValueError(f"sport inválido: {sport!r}. Use 'nba' ou 'futebol'.")

# Season Configuration
try:
    SEASON = int(os.getenv("SEASON", "2025"))
except (TypeError, ValueError) as e:
    raise RuntimeError(f"SEASON inválido: {os.getenv('SEASON')!r} (esperado inteiro)") from e

# Temporadas a extrair (backfill + corrente)
SEASONS = [2023, 2024, 2025]

# Data de fim para temporadas já encerradas (corrente usa datetime.now())
NBA_SEASON_END_DATES = {
    2023: "2024-06-17",  # Finals 2023-24: Boston x Dallas
    2024: "2025-06-22",  # Finals 2024-25 (estimado)
}

# Janelas play-in e playoffs por temporada
NBA_SEASON_TYPE_DATES = {
    2023: {"playin_start": "2024-04-16", "playoffs_start": "2024-04-20"},
    2024: {"playin_start": "2025-04-15", "playoffs_start": "2025-04-19"},
    2025: {"playin_start": "2026-04-14", "playoffs_start": "2026-04-18"},
}


def get_season_type_for_date(game_date: str, season: int) -> str:
    """Retorna 'regular', 'playin' ou 'playoffs' com base na data do jogo."""
    dates = NBA_SEASON_TYPE_DATES.get(season, {})
    if not dates:
        return "regular"
    if game_date >= dates.get("playoffs_start", "9999"):
        return "playoffs"
    if game_date >= dates.get("playin_start", "9999"):
        return "playin"
    return "regular"

# Per-game NBA (betting_odds / player_props): skip-if-exists + janela de re-fetch.
# Mesma mecânica dos per-fixture de futebol — odds/props de jogos antigos não mudam,
# então pula jogos cujo arquivo já existe no GCS, exceto os dos últimos N dias (re-busca
# correções/linhas que ainda estavam abrindo). Evita reprocessar milhares de jogos a cada
# run (quota/tempo) perto do fim da temporada. NBA não expõe data por game_id facilmente
# aqui, então a janela é aplicada por skip-if-exists puro (re-fetch só quando ainda não há
# arquivo). PER_GAME_SLEEP_SECONDS é a cortesia entre chamadas (rate-limit), adicionada
# JUNTO do skip-if-exists para não agravar o timeout reprocessando tudo.
NBA_PER_GAME_REFETCH_WINDOW_DAYS = 2
NBA_PER_GAME_SLEEP_SECONDS = 0.2

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Endpoint-specific configurations
ENDPOINT_CONFIGS = {
    "games": {
        "has_date": True,
        "per_page": 100,
    },
    "game_player_stats": {
        "has_date": True,
        "per_page": 100,
    },
    "season_averages": {
        "has_date": False,
        "per_page": 100,
    },
    "team_season_averages": {
        "has_date": False,
        "per_page": 100,
    },
    "active_players": {
        "has_date": False,
        "per_page": 100,
    },
    "player_injuries": {
        "has_date": False,
        "per_page": 100,
    },
    "team_standings": {
        "has_date": False,
        "per_page": 100,
    },
    "player_props": {
        "has_date": True,
        "per_page": 100,
        "market": "draftkings",
        "vendors": [
            "draftkings",
            "caesars",
            "betrivers",
        ],
    },
    "betting_odds": {
        "has_date": False,
        "per_page": 100,
    },
    "game_player_stats_period": {
        "has_date": True,
        "per_page": 100,
        "periods": [1, 2, 3, 4],
    },
    "game_player_advanced_stats": {
        "has_date": True,
        "per_page": 100,
    },
}

# Combinações de season_averages / team_season_averages — FONTE ÚNICA.
# Tanto os scripts de extração (scripts/extract_season_averages.py,
# scripts/extract_team_season_averages.py) quanto a criação das external tables
# (src/bigquery/bigquery_client.py) importam estas listas daqui. Antes estavam
# duplicadas em ambos os lados, exigindo sincronização manual (um novo type só num
# lugar gerava tabela apontando p/ arquivo inexistente ou vice-versa).
SEASON_AVERAGES_COMBINATIONS = [
    {"category": "general", "type": "base"},
    {"category": "general", "type": "advanced"},
    {"category": "shooting", "type": "by_zone"},
    {"category": "tracking", "type": "passing"},
]

TEAM_SEASON_AVERAGES_COMBINATIONS = [
    {"category": "general",   "type": "advanced"},
    {"category": "general",   "type": "opponent"},
    {"category": "general",   "type": "defense"},
    {"category": "tracking",  "type": "rebounding"},
    # Opp Rankings — Play Types
    {"category": "playtype",     "type": "isolation"},
    {"category": "playtype",     "type": "transition"},
    {"category": "playtype",     "type": "spotup"},
    {"category": "playtype",     "type": "handoff"},
    {"category": "playtype",     "type": "cut"},
    {"category": "playtype",     "type": "offscreen"},
    {"category": "playtype",     "type": "postup"},
    {"category": "playtype",     "type": "prballhandler"},
    {"category": "playtype",     "type": "prrollman"},
    {"category": "playtype",     "type": "offrebound"},
    # Opp Rankings — Hustle / Rim Protection
    {"category": "hustle",       "type": "overall"},
    {"category": "tracking",     "type": "defense"},
    # Opp Rankings — C&S / Pull Up (shotdashboard)
    {"category": "shotdashboard", "type": "catch_and_shoot"},
    {"category": "shotdashboard", "type": "pullups"},
    # Opp Rankings — Shooting cedido (defesa por zona / faixa de distância)
    {"category": "shooting", "type": "by_zone_opponent"},
    {"category": "shooting", "type": "5ft_range_opponent"},
]

# Tipos de temporada extraídos para (team_)season_averages.
SEASON_TYPES = ["regular", "playoffs", "ist"]


def require_env(name: str) -> str:
    """Lê uma env var obrigatória, levantando erro claro se ausente/vazia.

    Helper de uso OPCIONAL (não levanta no import-time deste módulo): os scripts
    podem chamá-lo quando quiserem falhar cedo com mensagem acionável em vez de
    propagar um erro obscuro mais adiante.
    """
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return value


def _int_env(name: str, default: int) -> int:
    """Lê uma env var inteira com fallback, levantando erro claro se inválida.

    Helper de uso OPCIONAL (não chamado no import-time). Útil para janelas/limites
    configuráveis por ambiente sem espalhar try/except de int() pelos scripts.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"{name} inválido: {raw!r} (esperado inteiro)") from e


def use_backfill_seasons() -> bool:
    """Centraliza a leitura da flag BACKFILL_SEASONS.

    BACKFILL_SEASONS truthy → iterar todas as SEASONS (uso pontual/backfill).
    Sem a var (ou vazia) → só a SEASON corrente (padrão Cloud Run).
    """
    return bool(os.getenv("BACKFILL_SEASONS"))


def get_seasons_to_process() -> list:
    """Resolve as seasons a processar conforme a flag BACKFILL_SEASONS."""
    return SEASONS if use_backfill_seasons() else [SEASON]


# Status HTTP que, em (team_)season_averages, significam "combinação sem dados"
# (a balldontlie devolve 400/404/422 p/ combos category/type inexistentes naquela
# temporada/season_type) e devem ser tratados como skip, não como erro real.
HTTP_NO_DATA_STATUS = (400, 404, 422)


def is_http_no_data(exc) -> bool:
    """True se a exceção for um HTTPError de "sem dados" (400/404/422).

    Centraliza a classificação antes duplicada entre os scripts de season averages.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) if response is not None else None
    return status_code in HTTP_NO_DATA_STATUS


def get_mode(env_var: str, default: str = "current") -> str:
    """Centraliza a leitura de env vars de modo dos extractors de futebol.

    Ex.: get_mode("STANDINGS_MODE"). Mantém o default "current" usado hoje pelos
    scripts; passa a haver um único ponto caso a semântica de modo mude.
    """
    return os.getenv(env_var, default)


# GCS Path Structure
def get_gcs_path(
    endpoint: str,
    season: int,
    date: str = None,
    market: str = None,
    game_id: int = None,
    category: str = None,
    type: str = None,
    season_type: str = None,
    period: int = None,
    sport: str = "nba",
    mode: str = None,
) -> str:
    """
    Gera o caminho no GCS seguindo a estrutura definida.

    Args:
        endpoint: Nome do endpoint (ex: 'games', 'active_players')
        season: Ano da temporada (ex: 2025)
        date: Data no formato YYYY-MM-DD (opcional)
        market: Market para player_props (opcional)
        game_id: Game ID para player_props (opcional)
        category: Categoria para season_averages (opcional)
        type: Tipo para season_averages (opcional)
        season_type: Tipo de temporada para season_averages (ex: regular, playoffs, ist)
        period: Quarto/período (NBA) — grava em subdiretório q{period}/ quando combinado com date
        sport: Identificador do esporte ("nba" default, "futebol" para API-Football)
        mode: Sufixo de modo para endpoints futebol ("current"|"backfill"), opcional

    Returns:
        Caminho completo no formato: nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}.json
        ou nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{date}.json
        ou nba/{endpoint}/{season}/{market}/raw_nba_{endpoint}_{season}-{game_id}.json (para player_props)
        ou nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{category}-{type}-{season_type}.json (para season_averages)
        ou futebol/{endpoint}/raw_futebol_{endpoint}{_mode}.json (para sport='futebol')
        ou futebol/{endpoint}/raw_futebol_{endpoint}{_mode}_{date}.json (futebol com date — snapshots diários)
    """
    # Branch dedicado para sport='futebol' (não polui a lógica NBA existente)
    if sport == "futebol":
        # Endpoints per-fixture (ex: fixture_statistics) salvam 1 arquivo por jogo,
        # reusando o param game_id como fixture_id:
        # futebol/{endpoint}/raw_futebol_{endpoint}_{fixture_id}.json
        # fixture_lineups grava em duas fases (mode "confirmed"|"real") → sufixo de fase
        # no nome do arquivo p/ guardar os dois snapshots (T-30min e pós-jogo).
        # odds grava em três janelas (mode "t24h"|"t1h"|"t15m") → mesmo mecanismo de sufixo.
        if game_id is not None:
            # Fases válidas: janelas de odds + de predictions (fonte única: as constantes
            # FUTEBOL_*_WINDOWS) + fases de lineups ("confirmed"|"real"). Novas janelas
            # funcionam sem editar aqui.
            phase = (
                f"_{mode}"
                if mode in {"confirmed", "real", *FUTEBOL_ODDS_WINDOWS, *FUTEBOL_PREDICTIONS_WINDOWS, *FUTEBOL_INJURIES_WINDOWS}
                else ""
            )
            # `date` opcional: predictions date-stampa o snapshot por (fixture, janela, dia)
            # p/ recapturar 1x/dia (varredura de jogos futuros) — o fato dedup latest-wins por
            # loaded_at. Odds/lineups não passam date → datepart vazio → nomes atuais intactos.
            datepart = f"_{date}" if date else ""
            filename = f"raw_futebol_{endpoint}_{game_id}{phase}{datepart}.json"
            return f"futebol/{endpoint}/{filename}"
        # `date` opcional: endpoints de snapshot diário (ex.: standings) date-stampam
        # o arquivo p/ acumular histórico no GCS (1 arquivo/dia; re-run no mesmo dia
        # sobrescreve). Os demais endpoints não passam date → latest-only, como antes.
        suffix = f"_{mode}" if mode in ("backfill", "current") else ""
        datepart = f"_{date}" if date else ""
        filename = f"raw_futebol_{endpoint}{suffix}{datepart}.json"
        return f"futebol/{endpoint}/{filename}"

    if market and game_id:
        # Estrutura especial para player_props: nba/player_props/{season}/{market}/raw_nba_player_props_{season}-{game_id}.json
        filename = f"raw_nba_{endpoint}_{season}-{game_id}.json"
        return f"nba/{endpoint}/{season}/{market}/{filename}"
    elif game_id and not market:
        # Estrutura para endpoints com game_id sem vendor: nba/betting_odds/{season}/raw_nba_betting_odds_{season}-{game_id}.json
        filename = f"raw_nba_{endpoint}_{season}-{game_id}.json"
        return f"nba/{endpoint}/{season}/{filename}"
    elif category and type:
        # Estrutura para season_averages: nba/season_averages/{season}/raw_nba_season_averages_{season}-{category}-{type}-{season_type}.json
        suffix = f"-{season_type}" if season_type else ""
        filename = f"raw_nba_{endpoint}_{season}-{category}-{type}{suffix}.json"
        return f"nba/{endpoint}/{season}/{filename}"
    elif period and date:
        filename = f"raw_nba_{endpoint}_{season}-{date}.json"
        return f"nba/{endpoint}/{season}/q{period}/{filename}"
    elif date:
        filename = f"raw_nba_{endpoint}_{season}-{date}.json"
        return f"nba/{endpoint}/{season}/{filename}"
    else:
        filename = f"raw_nba_{endpoint}_{season}.json"
        return f"nba/{endpoint}/{season}/{filename}"

