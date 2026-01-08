import functions_framework
import sys
import os

# Adicionar diretório atual ao path para Cloud Run
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Adicionar diretório raiz do projeto ao path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

from src.extractors.player_injuries_extractor import PlayerInjuriesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    """Extrai lesões de jogadores."""
    try:
        logger.info(f"Iniciando extração de lesões para temporada {SEASON}")
        extractor = PlayerInjuriesExtractor(season=SEASON)
        gcs_path = extractor.extract_and_save()
        logger.info(f"✓ Extração concluída: {gcs_path}")
        return {"status": "success", "message": "Extração concluída com sucesso", "gcs_path": gcs_path}
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        raise e


@functions_framework.http
def extract_player_injuries(request):
    """NBA Player Injuries Pipeline"""
    try:
        result = main()
        return result, 200
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
