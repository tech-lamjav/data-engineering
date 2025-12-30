"""Script individual para extração de médias da temporada."""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.season_averages_extractor import SeasonAveragesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Extrair médias da temporada")
    parser.add_argument(
        "--season",
        type=int,
        default=SEASON,
        help=f"Ano da temporada (default: {SEASON})",
    )
    parser.add_argument(
        "--player-ids",
        type=int,
        nargs="+",
        help="IDs dos jogadores (opcional)",
    )
    
    args = parser.parse_args()
    
    try:
        logger.info(f"Iniciando extração de médias para temporada {args.season}")
        extractor = SeasonAveragesExtractor(season=args.season)
        gcs_path = extractor.extract_and_save(
            player_ids=args.player_ids,
        )
        logger.info(f"✓ Extração concluída: {gcs_path}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

