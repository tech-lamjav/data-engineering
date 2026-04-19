"""Script para criar external tables no dataset sandbox do BigQuery."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.bigquery import BigQueryClient
from src.config import SEASON, ENDPOINT_CONFIGS
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

SANDBOX_DATASET = "sandbox"


def main():
    """Função principal."""
    try:
        season = SEASON

        logger.info("=" * 60)
        logger.info("Criando external tables no dataset sandbox")
        logger.info(f"Projeto: smartbetting-dados")
        logger.info(f"Dataset: {SANDBOX_DATASET}")
        logger.info(f"Temporada: {season}")
        logger.info("=" * 60)

        bq_client = BigQueryClient(dataset_id=SANDBOX_DATASET)

        # Garante que o dataset sandbox existe
        bq_client.create_dataset_if_not_exists()

        # External table completa de player_props (todos os vendors)
        # BigQuery não suporta múltiplos wildcards, então passamos uma URI por vendor
        vendors = ENDPOINT_CONFIGS["player_props"]["vendors"]
        uris = bq_client.get_player_props_uris(season=season, vendors=vendors)
        bq_client.create_external_table(
            table_id="raw_player_props_complete",
            uri=uris,
            description="External table completa de props de jogadores - todos os markets/vendors",
        )
        logger.info(f"✓ External table criada: {SANDBOX_DATASET}.raw_player_props_complete")
        logger.info(f"  URIs: {uris}")

        logger.info("=" * 60)
        logger.info("Sandbox external tables criadas com sucesso")
        return 0

    except Exception as e:
        logger.error(f"Erro ao criar sandbox external tables: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
