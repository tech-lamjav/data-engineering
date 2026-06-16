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

# Split entre backfill (one-shot, anos anteriores) e current (diário, ano corrente)
LEAGUES_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
]
LEAGUES_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
]

# Idem leagues — 4 chamadas distribuídas entre backfill (one-shot) e current (diário).
TEAMS_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
]
TEAMS_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
]

# Idem teams — catálogo de jogadores via /players?league=&season= (paginado).
PLAYERS_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
]
PLAYERS_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
]

# Fixtures (jogos) — tabela mãe via /fixtures?league=&season= (paginado).
# Cada fixture_id destrava stats/events/lineups/player_stats (subtasks 5-8).
FIXTURES_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
]
FIXTURES_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
]

# Standings (/standings) — snapshot diário da tabela do campeonato (1 chamada por
# liga×season, ~20 linhas Brasileirão). Diferente dos demais (latest-only), o arquivo
# é date-stampado (raw_futebol_standings_{mode}_{YYYY-MM-DD}.json): o GCS acumula 1
# snapshot por dia (histórico de evolução da tabela) e re-rodar no mesmo dia
# sobrescreve o mesmo arquivo (idempotente). A API não tem histórico diário — o
# backfill captura a tabela FINAL de 2024/2025 com snapshot_date do dia da coleta.
STANDINGS_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
]
STANDINGS_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
]

# Injuries (/injuries) — snapshot diário de lesionados/suspensos (1 chamada/liga×season,
# NÃO paginada — como /fixtures, rejeita `page` e devolve o log de lesões da temporada
# INTEIRA, ~2k linhas/liga). Mesma mecânica date-stampada do standings
# (raw_futebol_injuries_{mode}_{data}.json): o GCS acumula 1 snapshot/dia e re-rodar no
# mesmo dia sobrescreve (idempotente). ⚠️ A API repete linhas EXATAS — dedup no fato.
# ⚠️ Coverage validado em dim_leagues (2026-06-15): coverage.injuries = TRUE só p/
# Brasileirão (71) 2024/25/26. Copa do Mundo (1) 2026 = FALSE → EXCLUÍDA (a API não
# fornece lesões da Copa; incluí-la gastaria quota e voltaria vazia).
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
# janela): raw_futebol_odds_{fixture}_{t24h|t1h}.json (sufixo de fase no get_gcs_path).
#
# FUTEBOL_ODDS_WINDOWS: banda (lead_min, lead_max) em MINUTOS até o kickoff. A 1ª passada
# do poll com lead na banda captura; skip-if-exists trava as seguintes (1 captura/janela).
# Banda > intervalo de poll p/ não furar; a captura cai perto de lead_max (lead decresce
# no tempo). minutes_to_kickoff exato é registrado no fato — a precisão de CLV não depende
# da largura da banda. Extensível: somar "t15m": (0, 15) no futuro (linha de fechamento real).
FUTEBOL_ODDS_WINDOWS = {
    "t24h": (1320, 1440),  # 22h–24h antes (alvo 24h — linha de abertura)
    "t1h":  (30, 60),      # 30–60min antes (alvo 1h — linha perto do fechamento)
}

# Ligas com coverage.odds=TRUE (validado em dim_leagues). O poll filtra os jogos NS
# por esses league_ids. Diferente de /injuries (Copa excluída), odds de Copa do Mundo
# normalmente existem — manter 1 aqui se a validação confirmar coverage.odds=TRUE.
FUTEBOL_ODDS_LEAGUE_IDS = [BRASILEIRAO_ID, COPA_MUNDO_ID]

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
BIGQUERY_PROJECT_ID = "smartbetting-dados"
BIGQUERY_DATASET = "nba"
BIGQUERY_DATASET_FUTEBOL = "futebol"
BIGQUERY_LOCATION = "us-east1"

# Supabase Postgres sync configuration
# Connection strings DEVEM usar porta 5432 (sessão direta), NÃO 6543 (pgbouncer):
# pgbouncer em modo transaction não suporta COPY nem prepared statements.
# Dois ambientes: PRD recebe sync agendado via workflow, DEV idem (sequencial).
SUPABASE_PG_URL_PRD = os.getenv("SUPABASE_PG_URL_PRD")
SUPABASE_PG_URL_DEV = os.getenv("SUPABASE_PG_URL_DEV")
MART_PG_SCHEMA = "nba_mart"


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

# Season Configuration
SEASON = int(os.getenv("SEASON", "2025"))

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
        # odds grava em duas janelas (mode "t24h"|"t1h") → mesmo mecanismo de sufixo.
        if game_id is not None:
            phase = f"_{mode}" if mode in ("confirmed", "real", "t24h", "t1h") else ""
            filename = f"raw_futebol_{endpoint}_{game_id}{phase}.json"
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

