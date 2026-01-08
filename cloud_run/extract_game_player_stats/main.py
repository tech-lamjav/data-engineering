import functions_framework
import sys
import os

# Adicionar diretório atual ao path para Cloud Run
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Adicionar diretório raiz do projeto ao path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

from src.extractors.game_player_stats_extractor import GamePlayerStatsExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    """Extrai estatísticas de jogadores por jogo."""
    try:
        logger.info(f"Iniciando extração de estatísticas para temporada {SEASON}")
        extractor = GamePlayerStatsExtractor(season=SEASON)
        gcs_paths = extractor.extract_and_save()
        
        if gcs_paths:
            logger.info(f"✓ Extração concluída: {len(gcs_paths)} arquivo(s) salvo(s)")
            return {
                "status": "success",
                "message": "Extração concluída com sucesso",
                "files_count": len(gcs_paths),
                "gcs_paths": gcs_paths
            }
        else:
            logger.warning("Nenhum arquivo foi salvo.")
            return {
                "status": "warning",
                "message": "Nenhum arquivo foi salvo"
            }
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        raise e


@functions_framework.http
def extract_game_player_stats(request):
    """NBA Game Player Stats Pipeline"""
    try:
        result = main()
        return result, 200
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
