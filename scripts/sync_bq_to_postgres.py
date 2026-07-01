"""Script para sincronizar marts BigQuery -> Supabase Postgres.

Para testar localmente:
    SYNC_ENV=dev python scripts/sync_bq_to_postgres.py                 (NBA, default)
    SYNC_ENV=dev SYNC_SPORT=futebol python scripts/sync_bq_to_postgres.py
    SYNC_ENV=prd python scripts/sync_bq_to_postgres.py
    # subset: SYNC_TABLES=fact_value_opportunities,fact_fixtures
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.bq_to_postgres import run_sync
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    env = os.getenv("SYNC_ENV", "prd")
    sport = os.getenv("SYNC_SPORT", "nba")
    tables = os.getenv("SYNC_TABLES", "all")
    try:
        result = run_sync(tables=tables, env=env, sport=sport)
        if result["status"] == "aborted_schema_drift":
            logger.error(f"Sync abortado por schema drift: {result['drift']}")
            return 2
        logger.info(f"Sync concluído: {result}")
        return 0
    except Exception as e:
        logger.error(f"Erro: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
