"""Script para extração de player props - vendor BetRivers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.player_props_extractor import PlayerPropsExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

VENDOR = "betrivers"


def main():
    try:
        extractor = PlayerPropsExtractor(season=SEASON)
        logger.info(f"Extraindo props ({VENDOR}) para temporada {SEASON}")
        gcs_paths = extractor.extract_and_save(vendors=[VENDOR])
        if gcs_paths:
            logger.info(f"✓ {len(gcs_paths)} arquivo(s) salvos")
            for path in gcs_paths:
                logger.info(f"  → {path}")
        else:
            logger.warning("Nenhum arquivo salvo.")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
