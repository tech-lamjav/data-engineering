"""Cloud Function para extração de lesões de jogadores."""
import json
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para importar módulos
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.player_injuries_extractor import PlayerInjuriesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def extract_player_injuries(request):
    """
    Cloud Function HTTP trigger para extração de lesões de jogadores.
    
    Args:
        request: Flask request object
    
    Returns:
        JSON response com status da execução
    """
    try:
        # Parse request JSON se fornecido
        request_json = request.get_json(silent=True) or {}
        season = request_json.get("season", SEASON)
        
        logger.info(f"Iniciando extração de lesões de jogadores para temporada {season}")
        
        # Cria extractor e executa
        extractor = PlayerInjuriesExtractor(season=season)
        gcs_path = extractor.extract_and_save()
        
        return {
            "status": "success",
            "message": "Extração de lesões concluída",
            "gcs_path": gcs_path,
            "season": season,
        }, 200
        
    except Exception as e:
        logger.error(f"Erro na extração de lesões: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
        }, 500


# Para testes locais com Functions Framework
# Instale: pip install functions-framework
# Execute: functions-framework --target=extract_player_injuries --port=8080

