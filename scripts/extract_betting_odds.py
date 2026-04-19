"""Script para extração de betting odds."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.betting_odds_extractor import BettingOddsExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        gcs_paths = BettingOddsExtractor(season=SEASON).extract_and_save()
        if gcs_paths:
            logger.info(f"{len(gcs_paths)} arquivo(s) salvos")
            for path in gcs_paths:
                logger.info(f"  -> {path}")
        else:
            logger.warning("Nenhum arquivo salvo.")
        return 0
    except Exception as e:
        logger.error(f"Erro: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
