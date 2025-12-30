"""Cloud Function para extração de estatísticas de jogadores por jogo."""
import json
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para importar módulos
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.game_player_stats_extractor import GamePlayerStatsExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def extract_game_player_stats(request):
    """
    Cloud Function HTTP trigger para extração de estatísticas de jogadores.
    
    Args:
        request: Flask request object
    
    Returns:
        JSON response com status da execução
    """
    try:
        # Parse request JSON se fornecido
        request_json = request.get_json(silent=True) or {}
        season = request_json.get("season", SEASON)
        game_ids = request_json.get("game_ids")
        player_ids = request_json.get("player_ids")
        date = request_json.get("date")
        
        logger.info(f"Iniciando extração de estatísticas de jogadores para temporada {season}")
        
        # Cria extractor e executa
        extractor = GamePlayerStatsExtractor(season=season)
        gcs_path = extractor.extract_and_save(
            game_ids=game_ids,
            player_ids=player_ids,
            date=date,
        )
        
        return {
            "status": "success",
            "message": "Extração de estatísticas concluída",
            "gcs_path": gcs_path,
            "season": season,
        }, 200
        
    except Exception as e:
        logger.error(f"Erro na extração de estatísticas: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
        }, 500


# Para testes locais com Functions Framework
# Instale: pip install functions-framework
# Execute: functions-framework --target=extract_game_player_stats --port=8080

