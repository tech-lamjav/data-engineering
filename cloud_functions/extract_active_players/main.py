"""Cloud Function para extração de jogadores ativos."""
import json
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para importar módulos
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.active_players_extractor import ActivePlayersExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def extract_active_players(request):
    """
    Cloud Function HTTP trigger para extração de jogadores ativos.
    
    Args:
        request: HTTP request object (Flask ou Functions Framework)
    
    Returns:
        Tuple (response_dict, status_code) ou response_dict
    """
    try:
        # Parse request JSON se fornecido
        request_json = request.get_json(silent=True) or {}
        season = request_json.get("season", SEASON)
        
        logger.info(f"Iniciando extração de jogadores ativos para temporada {season}")
        
        # Cria extractor e executa
        extractor = ActivePlayersExtractor(season=season)
        gcs_path = extractor.extract_and_save()
        
        return {
            "status": "success",
            "message": "Extração de jogadores ativos concluída",
            "gcs_path": gcs_path,
            "season": season,
        }, 200
        
    except Exception as e:
        logger.error(f"Erro na extração de jogadores ativos: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
        }, 500


# Para testes locais com Functions Framework
# Instale: pip install functions-framework
# Execute: functions-framework --target=extract_active_players --port=8080

