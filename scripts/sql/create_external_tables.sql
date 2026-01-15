-- ============================================================
-- Script SQL para criar external tables no BigQuery
-- Dataset: nba
-- Project: smartbetting-dados
-- Location: us-east1
-- ============================================================

-- Substitua as variáveis abaixo pelos seus valores:
-- ${GCS_BUCKET_NAME}: Nome do bucket GCS (padrão: smartbetting-landing)
-- ${SEASON}: Temporada (ex: 2025)

-- ============================================================
-- Criar dataset (execute apenas uma vez)
-- ============================================================
CREATE SCHEMA IF NOT EXISTS `smartbetting-dados.nba`
OPTIONS(
  location="us-east1",
  description="Dataset para external tables dos dados brutos da NBA"
);

-- ============================================================
-- External Tables - Endpoints SEM data (arquivo único)
-- ============================================================

-- Active Players
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_active_players`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/active_players/${SEASON}/raw_nba_active_players_${SEASON}.json"],
  description="External table para dados brutos de jogadores ativos"
);

-- Season Averages - General Base
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_season_averages_general_base`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/season_averages/${SEASON}/raw_nba_season_averages_${SEASON}-general-base.json"],
  description="External table para dados brutos de médias da temporada (category=general, type=base)"
);

-- Season Averages - General Advanced
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_season_averages_general_advanced`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/season_averages/${SEASON}/raw_nba_season_averages_${SEASON}-general-advanced.json"],
  description="External table para dados brutos de médias da temporada (category=general, type=advanced)"
);

-- Season Averages - Shooting By Zone
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_season_averages_shooting_by_zone`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/season_averages/${SEASON}/raw_nba_season_averages_${SEASON}-shooting-by_zone.json"],
  description="External table para dados brutos de médias da temporada (category=shooting, type=by_zone)"
);

-- Player Injuries
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_player_injuries`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/player_injuries/${SEASON}/raw_nba_player_injuries_${SEASON}.json"],
  description="External table para dados brutos de lesões de jogadores"
);

-- Team Standings
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_standings`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_standings/${SEASON}/raw_nba_team_standings_${SEASON}.json"],
  description="External table para dados brutos de classificação de times"
);

-- ============================================================
-- External Tables - Endpoints COM data (múltiplos arquivos)
-- ============================================================

-- Games (usa wildcard para ler todos os arquivos por data)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_games`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/games/${SEASON}/*.json"],
  description="External table para dados brutos de jogos (inclui múltiplos arquivos por data)"
);

-- Game Player Stats (usa wildcard para ler todos os arquivos por data)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_game_player_stats`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/game_player_stats/${SEASON}/*.json"],
  description="External table para dados brutos de estatísticas de jogadores por jogo (inclui múltiplos arquivos por data)"
);

-- Player Props (usa wildcard para ler todos os arquivos por data)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_player_props`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/player_props/${SEASON}/*/*.json"],
  description="External table para dados brutos de props de jogadores (inclui múltiplos arquivos por data e market)"
);

-- ============================================================
-- Verificar tabelas criadas
-- ============================================================
SELECT 
  table_name,
  table_type,
  creation_time,
  description
FROM `smartbetting-dados.nba.INFORMATION_SCHEMA.TABLES`
WHERE table_name LIKE 'raw_%'
ORDER BY table_name;
