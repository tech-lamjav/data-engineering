"""Cloud Function para extração de jogos."""
import json
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para importar módulos
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.games_extractor import GamesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def extract_games(request):
    """
    Cloud Function HTTP trigger para extração de jogos.
    
    Args:
        request: Flask request object
    
    Returns:
        JSON response com status da execução
    """
    try:
        # Parse request JSON se fornecido
        request_json = request.get_json(silent=True) or {}
        season = request_json.get("season", SEASON)
        team_ids = request_json.get("team_ids")
        dates = request_json.get("dates")
        
        logger.info(f"Iniciando extração de jogos para temporada {season}")
        
        # Cria extractor e executa
        extractor = GamesExtractor(season=season)
        gcs_path = extractor.extract_and_save(
            team_ids=team_ids,
            dates=dates,
        )
        
        return {
            "status": "success",
            "message": "Extração de jogos concluída",
            "gcs_path": gcs_path,
            "season": season,
        }, 200
        
    except Exception as e:
        logger.error(f"Erro na extração de jogos: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
        }, 500


# Para testes locais com Functions Framework
# Instale: pip install functions-framework
# Execute: functions-framework --target=extract_games --port=8080

