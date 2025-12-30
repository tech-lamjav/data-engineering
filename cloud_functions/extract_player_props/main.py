"""Cloud Function para extração de props de jogadores."""
import json
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para importar módulos
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.player_props_extractor import PlayerPropsExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def extract_player_props(request):
    """
    Cloud Function HTTP trigger para extração de props de jogadores.
    
    Args:
        request: Flask request object
    
    Returns:
        JSON response com status da execução
    """
    try:
        # Parse request JSON se fornecido
        request_json = request.get_json(silent=True) or {}
        season = request_json.get("season", SEASON)
        prop_types = request_json.get("prop_types")
        date = request_json.get("date")
        fetch_all_prop_types = request_json.get("fetch_all_prop_types", True)
        
        logger.info(f"Iniciando extração de props de jogadores para temporada {season}")
        
        # Cria extractor e executa
        extractor = PlayerPropsExtractor(season=season)
        gcs_path = extractor.extract_and_save(
            prop_types=prop_types,
            date=date,
            fetch_all_prop_types=fetch_all_prop_types,
        )
        
        return {
            "status": "success",
            "message": "Extração de props concluída",
            "gcs_path": gcs_path,
            "season": season,
        }, 200
        
    except Exception as e:
        logger.error(f"Erro na extração de props: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
        }, 500


# Para testes locais com Functions Framework
# Instale: pip install functions-framework
# Execute: functions-framework --target=extract_player_props --port=8080

