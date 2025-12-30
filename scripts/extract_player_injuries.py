"""Script individual para extração de lesões de jogadores."""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.player_injuries_extractor import PlayerInjuriesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Extrair lesões de jogadores")
    parser.add_argument(
        "--season",
        type=int,
        default=SEASON,
        help=f"Ano da temporada (default: {SEASON})",
    )
    
    args = parser.parse_args()
    
    try:
        logger.info(f"Iniciando extração de lesões para temporada {args.season}")
        extractor = PlayerInjuriesExtractor(season=args.season)
        gcs_path = extractor.extract_and_save()
        logger.info(f"✓ Extração concluída: {gcs_path}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

