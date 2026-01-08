import functions_framework
import sys
import os

# Adicionar diretório atual ao path para Cloud Run
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Adicionar diretório raiz do projeto ao path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

from src.extractors.team_standings_extractor import TeamStandingsExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    """Extrai classificação de times."""
    try:
        logger.info(f"Iniciando extração de classificação para temporada {SEASON}")
        extractor = TeamStandingsExtractor(season=SEASON)
        gcs_path = extractor.extract_and_save()
        logger.info(f"✓ Extração concluída: {gcs_path}")
        return {"status": "success", "message": "Extração concluída com sucesso", "gcs_path": gcs_path}
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        raise e


@functions_framework.http
def extract_team_standings(request):
    """NBA Team Standings Pipeline"""
    try:
        result = main()
        return result, 200
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
