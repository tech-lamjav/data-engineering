import functions_framework
import logging
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

project_root = current_dir
sys.path.insert(0, project_root)

scripts_dir = os.path.join(project_root, "scripts")
sys.path.insert(0, scripts_dir)

from src.sync.bq_to_postgres import run_sync


@functions_framework.http
def sync_bq_to_postgres(request):
    """Sync BigQuery marts -> Supabase Postgres.

    Query params:
        env:    'prd' (default) ou 'dev'. Seleciona qual Supabase project.
        sport:  'nba' (default) ou 'futebol'. Resolve dataset BQ + schema
                Postgres + allowlist (config.get_sync_target).
        tables: 'all' (default) ou CSV de nomes, ex:
                ?sport=futebol&tables=fact_value_opportunities,fact_fixtures
    """
    env = request.args.get("env", default="prd")
    sport = request.args.get("sport", default="nba")
    tables = request.args.get("tables", default="all")
    try:
        result = run_sync(tables=tables, env=env, sport=sport)
        if result["status"] == "aborted_schema_drift":
            return {
                "status": "aborted_schema_drift",
                "sport": result["sport"],
                "env": result["env"],
                "drift": result["drift"],
            }, 500
        return {
            "status": "success",
            "sport": result["sport"],
            "env": result["env"],
            "summary": result.get("summary", {}),
            "synced": result["synced"],
        }, 200
    except Exception as e:
        # Não ecoar str(e) ao chamador (pode vazar DSN/host/schema do Postgres).
        # O traceback completo fica no Cloud Logging.
        logging.getLogger("sync_bq_to_postgres").error(
            f"Falha no sync (sport={sport}, env={env}): {e}", exc_info=True
        )
        return {"status": "error", "sport": sport, "env": env}, 500
