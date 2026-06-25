-- ============================================================
-- [DEPRECATED] NÃO é a fonte de verdade. As external tables são criadas pelos
-- geradores Python: scripts/create_bigquery_external_tables.py (NBA) e
-- scripts/futebol/create_external_tables.py (futebol). Este .sql tem
-- project/dataset/location hardcoded e PODE divergir — mantido só como referência.
-- ------------------------------------------------------------
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

-- Season Averages - General Base (cobre regular, playoffs e ist via wildcard)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_season_averages_general_base`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/season_averages/${SEASON}/raw_nba_season_averages_${SEASON}-general-base-*.json"],
  description="External table para dados brutos de médias da temporada (category=general, type=base) - todos os season_types"
);

-- Season Averages - General Advanced (cobre regular, playoffs e ist via wildcard)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_season_averages_general_advanced`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/season_averages/${SEASON}/raw_nba_season_averages_${SEASON}-general-advanced-*.json"],
  description="External table para dados brutos de médias da temporada (category=general, type=advanced) - todos os season_types"
);

-- Season Averages - Shooting By Zone (cobre regular, playoffs e ist via wildcard)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_season_averages_shooting_by_zone`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/season_averages/${SEASON}/raw_nba_season_averages_${SEASON}-shooting-by_zone-*.json"],
  description="External table para dados brutos de médias da temporada (category=shooting, type=by_zone) - todos os season_types"
);

-- Season Averages - Tracking Passing (passes, secondary_assists, free_throw_assists, potential_assists)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_season_averages_tracking_passing`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/season_averages/${SEASON}/raw_nba_season_averages_${SEASON}-tracking-passing-*.json"],
  description="Tracking passing stats por jogador (passes, secondary_assists, free_throw_assists) - category=tracking, type=passing - todos os season_types"
);

-- Team Season Averages - General Advanced (cobre regular, playoffs e ist via wildcard)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_general_advanced`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-general-advanced-*.json"],
  description="External table para dados brutos de médias da temporada por time (category=general, type=advanced) - todos os season_types"
);

-- Team Season Averages - General Opponent (stats que o time CEDE)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_general_opponent`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-general-opponent-*.json"],
  description="Stats que o time CEDE (rebotes, assists, pts, FG%, 3P%) - category=general, type=opponent - todos os season_types"
);

-- Team Season Averages - General Defense (DefRtg, opp FG%, opp 3PT%)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_general_defense`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-general-defense-*.json"],
  description="DefRtg, opp FG%, opp 3PT% cedidos - category=general, type=defense - todos os season_types"
);

-- Team Season Averages - Tracking Rebounding (rebote opportunities, contested rebounds)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_tracking_rebounding`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-tracking-rebounding-*.json"],
  description="Rebote opportunities, contested rebounds - category=tracking, type=rebounding - todos os season_types"
);

-- Team Season Averages - Playtype Isolation
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_isolation`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-isolation-*.json"],
  description="Play type isolation cedido: ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=isolation"
);

-- Team Season Averages - Playtype Transition
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_transition`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-transition-*.json"],
  description="Play type transition cedido: ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=transition"
);

-- Team Season Averages - Playtype Spot Up
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_spotup`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-spotup-*.json"],
  description="Play type spot up cedido: ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=spotup"
);

-- Team Season Averages - Playtype Handoff
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_handoff`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-handoff-*.json"],
  description="Play type handoff cedido: ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=handoff"
);

-- Team Season Averages - Playtype Cut
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_cut`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-cut-*.json"],
  description="Play type cut cedido: ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=cut"
);

-- Team Season Averages - Playtype Off Screen
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_offscreen`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-offscreen-*.json"],
  description="Play type off screen cedido: ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=offscreen"
);

-- Team Season Averages - Playtype Post Up
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_postup`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-postup-*.json"],
  description="Play type post up cedido: ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=postup"
);

-- Team Season Averages - Playtype PnR Ball Handler
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_prballhandler`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-prballhandler-*.json"],
  description="Play type PnR Ball Handler: ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=prballhandler"
);

-- Team Season Averages - Playtype PnR Roll Man
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_prrollman`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-prrollman-*.json"],
  description="Play type PnR Roll Man: ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=prrollman"
);

-- Team Season Averages - Playtype Off Rebound (Putback)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_playtype_offrebound`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-playtype-offrebound-*.json"],
  description="Play type Off Rebound (putback): ppp, poss_pct, fg_pct, efg_pct, percentile - category=playtype, type=offrebound"
);

-- Team Season Averages - Hustle Overall (contested shots, deflections, charges drawn)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_hustle_overall`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-hustle-overall-*.json"],
  description="Hustle stats: contested_shots, deflections, charges_drawn, screen_assists, box_outs - category=hustle, type=overall"
);

-- Team Season Averages - Tracking Defense (rim protection)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_tracking_defense`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-tracking-defense-*.json"],
  description="Rim protection: def_rim_fga, def_rim_fgm, def_rim_fg_pct - category=tracking, type=defense"
);

-- Team Season Averages - Shotdashboard Catch & Shoot
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_shotdashboard_catch_and_shoot`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-shotdashboard-catch_and_shoot-*.json"],
  description="Catch & Shoot shots: fga, fgm, fg2a, fg3a, fg_pct, efg_pct - category=shotdashboard, type=catch_and_shoot"
);

-- Team Season Averages - Shotdashboard Pull Ups
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_team_season_averages_shotdashboard_pullups`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/team_season_averages/${SEASON}/raw_nba_team_season_averages_${SEASON}-shotdashboard-pullups-*.json"],
  description="Pull Up shots: fga, fgm, fg2a, fg3a, fg_pct, efg_pct - category=shotdashboard, type=pullups"
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

-- Game Player Stats (wildcard cobre todas as temporadas e datas)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_game_player_stats`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/game_player_stats/*/*.json"],
  description="External table para dados brutos de estatísticas de jogadores por jogo — múltiplas temporadas. Cada stat record inclui campo season_type (regular/playoffs/playin)"
);

-- Game Player Stats Per Period (wildcard cobre todas as temporadas e quartos Q1-Q4)
-- Campo 'period' e 'season_type' embutidos em cada stat record
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_game_player_stats_period`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/game_player_stats_period/*/q*/*.json"],
  description="Stats por período/quarto (Q1-Q4) — múltiplas temporadas. 1H Points = period 1+2. season_type disponível por stat record"
);

-- Betting Odds (todos os vendors, usa wildcard por game_id)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_betting_odds`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/betting_odds/${SEASON}/*.json"],
  description="External table para dados brutos de betting odds (spread, moneyline, total) - todos os vendors"
);

-- Player Props DraftKings (somente market draftkings)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.nba.raw_player_props`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/player_props/${SEASON}/draftkings/*.json"],
  description="External table para dados brutos de props de jogadores - market DraftKings"
);

-- ============================================================
-- Dataset sandbox - External Table completa de Player Props
-- (todos os vendors/markets)
-- ============================================================
CREATE SCHEMA IF NOT EXISTS `smartbetting-dados.sandbox`
OPTIONS(
  location="us-east1",
  description="Dataset sandbox para exploração e análise de dados brutos"
);

-- Player Props completo (todos os markets/vendors via wildcard)
CREATE OR REPLACE EXTERNAL TABLE `smartbetting-dados.sandbox.raw_player_props_complete`
OPTIONS(
  format="JSON",
  uris=["gs://${GCS_BUCKET_NAME}/nba/player_props/${SEASON}/*/*.json"],
  description="External table completa de props de jogadores - todos os markets/vendors"
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
