"""Script individual para extração de jogos."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.games_extractor import GamesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        logger.info(f"Iniciando extração de jogos para temporada {SEASON}")
        extractor = GamesExtractor(season=SEASON)
        
        gcs_paths = extractor.extract_and_save()
        
        if gcs_paths:
            logger.info(f"✓ Extração concluída: {len(gcs_paths)} arquivo(s) salvo(s)")
            for path in gcs_paths:
                logger.info(f"  → {path}")
        else:
            logger.warning("Nenhum arquivo foi salvo.")
        
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

