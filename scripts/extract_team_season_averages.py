"""Script para extração de médias da temporada por time."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.team_season_averages_extractor import TeamSeasonAveragesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    """
    Extrai médias da temporada por time (category=general, type=advanced).
    Gera arquivo em nba/team_season_averages/{season}/raw_nba_team_season_averages_{season}-general-advanced.json
    """
    try:
        logger.info(f"Iniciando extração de team season averages para temporada {SEASON}")

        extractor = TeamSeasonAveragesExtractor(
            season=SEASON,
            category="general",
            type="advanced",
            season_type="regular",
        )
        gcs_path = extractor.extract_and_save()
        logger.info(f"✓ Extração concluída: {gcs_path}")
        return 0

    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
