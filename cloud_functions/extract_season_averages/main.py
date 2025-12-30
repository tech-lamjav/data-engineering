"""Cloud Function para extração de médias da temporada."""
import json
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para importar módulos
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.season_averages_extractor import SeasonAveragesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def extract_season_averages(request):
    """
    Cloud Function HTTP trigger para extração de médias da temporada.
    
    Args:
        request: Flask request object
    
    Returns:
        JSON response com status da execução
    """
    try:
        # Parse request JSON se fornecido
        request_json = request.get_json(silent=True) or {}
        season = request_json.get("season", SEASON)
        player_ids = request_json.get("player_ids")
        
        logger.info(f"Iniciando extração de médias da temporada {season}")
        
        # Cria extractor e executa
        extractor = SeasonAveragesExtractor(season=season)
        gcs_path = extractor.extract_and_save(
            player_ids=player_ids,
        )
        
        return {
            "status": "success",
            "message": "Extração de médias concluída",
            "gcs_path": gcs_path,
            "season": season,
        }, 200
        
    except Exception as e:
        logger.error(f"Erro na extração de médias: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
        }, 500


# Para testes locais com Functions Framework
# Instale: pip install functions-framework
# Execute: functions-framework --target=extract_season_averages --port=8080

