"""Script individual para extração de props de jogadores."""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.player_props_extractor import PlayerPropsExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Extrair props de jogadores (DraftKings)")
    parser.add_argument(
        "--season",
        type=int,
        default=SEASON,
        help=f"Ano da temporada (default: {SEASON})",
    )
    parser.add_argument(
        "--prop-types",
        type=str,
        nargs="+",
        help="Tipos de props (opcional, busca todas se não fornecido)",
    )
    parser.add_argument(
        "--date",
        type=str,
        help="Data no formato YYYY-MM-DD (opcional)",
    )
    parser.add_argument(
        "--no-fetch-all",
        action="store_true",
        help="Não buscar todas as prop types automaticamente",
    )
    
    args = parser.parse_args()
    
    try:
        logger.info(f"Iniciando extração de props para temporada {args.season}")
        extractor = PlayerPropsExtractor(season=args.season)
        gcs_path = extractor.extract_and_save(
            prop_types=args.prop_types,
            date=args.date,
            fetch_all_prop_types=not args.no_fetch_all,
        )
        logger.info(f"✓ Extração concluída: {gcs_path}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

