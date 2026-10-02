"""Script para sincronizar marts BigQuery -> Supabase Postgres.

Para testar localmente:
    SYNC_ENV=dev python scripts/sync_bq_to_postgres.py                 (NBA, default)
    SYNC_ENV=dev SYNC_SPORT=futebol python scripts/sync_bq_to_postgres.py
    SYNC_ENV=prd python scripts/sync_bq_to_postgres.py
    # subset: SYNC_TABLES=fact_value_opportunities,fact_fixtures
    # carga por troca (DE#108), só futebol: SYNC_TROCA=fact_fixtures  SYNC_STAGED=int_futebol_premissas_1x2
    # cache de serving das odds em PRD (DE#109), só futebol: SYNC_CACHE_SERVING=fact_odds_snapshot
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sync.bq_to_postgres import run_sync
from src.sync.trava import STATUS_OCUPADO
from src.sync.troca import STATUS_TROCA_FALHOU
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    env = os.getenv("SYNC_ENV", "prd")
    sport = os.getenv("SYNC_SPORT", "nba")
    tables = os.getenv("SYNC_TABLES", "all")
    troca = os.getenv("SYNC_TROCA", "")
    staged = os.getenv("SYNC_STAGED", "")
    cache_serving = os.getenv("SYNC_CACHE_SERVING", "")
    try:
        result = run_sync(
            tables=tables, env=env, sport=sport, troca=troca, staged=staged,
            cache_serving=cache_serving,
        )
        if result["status"] == "aborted_schema_drift":
            logger.error(f"Sync abortado por schema drift: {result['drift']}")
            return 2
        if result["status"] == STATUS_OCUPADO:
            # Outro sync do mesmo (sport, env) está com a trava (DE#107): nada foi tocado.
            logger.warning(f"Sync já em andamento para sport={result['sport']} env={result['env']}")
            return 3
        if result["status"] == STATUS_TROCA_FALHOU:
            # Tabela(s) habilitada(s) na troca que não trocaram (DE#108); as demais sincronizaram.
            logger.error(f"Sync parcial: troca falhou em {result['falhas']}")
            return 4
        logger.info(f"Sync concluído: {result}")
        return 0
    except Exception as e:
        logger.error(f"Erro: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
