import functions_framework
import sys
import os

# Adicionar diretório atual ao path para Cloud Run
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Adicionar diretório raiz do projeto ao path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
sys.path.insert(0, project_root)

from src.extractors.season_averages_extractor import SeasonAveragesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    """Extrai médias da temporada para diferentes combinações de category e type."""
    # Combinações de category e type a serem extraídas
    combinations = [
        {"category": "general", "type": "base"},
        {"category": "general", "type": "advanced"},
        {"category": "shooting", "type": "by_zone"},
    ]
    
    try:
        logger.info(f"Iniciando extração de médias para temporada {SEASON}")
        
        results = []
        gcs_paths = []
        
        for combo in combinations:
            category = combo["category"]
            type_param = combo["type"]
            
            logger.info(f"Processando: category={category}, type={type_param}")
            
            try:
                extractor = SeasonAveragesExtractor(
                    season=SEASON,
                    category=category,
                    type=type_param,
                    season_type="regular"
                )
                gcs_path = extractor.extract_and_save()
                logger.info(f"✓ Extração concluída para {category}-{type_param}: {gcs_path}")
                results.append({"category": category, "type": type_param, "path": gcs_path, "status": "success"})
                gcs_paths.append(gcs_path)
            except Exception as e:
                logger.error(f"✗ Erro na extração para {category}-{type_param}: {str(e)}", exc_info=True)
                results.append({"category": category, "type": type_param, "status": "error", "error": str(e)})
        
        # Verifica se todas as extrações foram bem-sucedidas
        all_success = all(r["status"] == "success" for r in results)
        
        if all_success:
            return {
                "status": "success",
                "message": "Extração concluída com sucesso",
                "files_count": len(gcs_paths),
                "gcs_paths": gcs_paths,
                "results": results
            }
        else:
            return {
                "status": "partial_success",
                "message": "Algumas extrações falharam",
                "files_count": len(gcs_paths),
                "gcs_paths": gcs_paths,
                "results": results
            }
            
    except Exception as e:
        logger.error(f"✗ Erro geral na extração: {str(e)}", exc_info=True)
        raise e


@functions_framework.http
def extract_season_averages(request):
    """NBA Season Averages Pipeline"""
    try:
        result = main()
        status_code = 200 if result["status"] == "success" else 207
        return result, status_code
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
