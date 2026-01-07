"""Script individual para extração de classificação de times."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.team_standings_extractor import TeamStandingsExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        logger.info(f"Iniciando extração de classificação para temporada {SEASON}")
        extractor = TeamStandingsExtractor(season=SEASON)
        gcs_path = extractor.extract_and_save()
        logger.info(f"✓ Extração concluída: {gcs_path}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

