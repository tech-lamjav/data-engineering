import functions_framework
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
        tables: 'all' (default) ou CSV de nomes, ex:
                ?tables=dim_daily_opportunities,ft_game_player_stats
    """
    env = request.args.get("env", default="prd")
    tables = request.args.get("tables", default="all")
    try:
        result = run_sync(tables=tables, env=env)
        if result["status"] == "aborted_schema_drift":
            return {
                "status": "aborted_schema_drift",
                "env": result["env"],
                "drift": result["drift"],
            }, 500
        return {
            "status": "success",
            "env": result["env"],
            "summary": result.get("summary", {}),
            "synced": result["synced"],
        }, 200
    except Exception as e:
        return {"status": "error", "env": env, "error": str(e)}, 500
