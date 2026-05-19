"""Script para sincronizar marts BigQuery -> Supabase Postgres.

Para testar localmente:
    SYNC_ENV=dev python scripts/sync_bq_to_postgres.py
    SYNC_ENV=prd python scripts/sync_bq_to_postgres.py  (default)
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
    try:
        result = run_sync(env=env)
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
