"""Script para criar external tables no BigQuery que leem arquivos JSON do GCS."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.bigquery import BigQueryClient
from src.config import SEASON, BIGQUERY_DATASET
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    """Função principal."""
    try:
        season = SEASON
        
        logger.info("=" * 60)
        logger.info("Criando external tables no BigQuery")
        logger.info(f"Projeto: smartbetting-dados")
        logger.info(f"Dataset: {BIGQUERY_DATASET}")
        logger.info(f"Temporada: {season}")
        logger.info("=" * 60)
        
        # Inicializa cliente BigQuery
        bq_client = BigQueryClient(dataset_id=BIGQUERY_DATASET)
        
        # Cria todas as external tables
        results = bq_client.create_all_external_tables(
            season=season,
            endpoints=None,  # Cria todas
        )
        
        logger.info("=" * 60)
        logger.info("Resumo:")
        logger.info("=" * 60)
        
        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)
        
        for endpoint, success in results.items():
            status = "✓" if success else "✗"
            logger.info(f"{status} {endpoint}")
        
        logger.info(f"\nTotal: {success_count}/{total_count} tabelas criadas com sucesso")
        
        if success_count == total_count:
            return 0
        else:
            return 1
            
    except Exception as e:
        logger.error(f"Erro ao criar external tables: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
