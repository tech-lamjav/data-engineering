CREATE SCHEMA IF NOT EXISTS futebol;
DROP TABLE IF EXISTS futebol.fact_odds_snapshot;
CREATE TABLE futebol.fact_odds_snapshot (
 competition text, league_id bigint, season bigint, fixture_id bigint, kickoff_utc timestamptz,
 collection_window text, collection_timestamp timestamptz, collection_date date, minutes_to_kickoff bigint,
 bookmaker_id bigint, bookmaker_name text, market_id bigint, market_name text, outcome_label text,
 outcome_side text, line_value double precision, odd_decimal double precision, api_update timestamptz,
 extracted_at timestamptz, dbt_loaded_at timestamptz);
