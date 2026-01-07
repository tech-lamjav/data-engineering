"""Script para extração de médias da temporada."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.season_averages_extractor import SeasonAveragesExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    """
    Extrai médias da temporada para diferentes combinações de category e type.
    Gera arquivos separados para cada combinação.
    """
    # Combinações de category e type a serem extraídas
    combinations = [
        {"category": "general", "type": "base"},
        {"category": "general", "type": "advanced"},
        {"category": "shooting", "type": "by_zone"},
    ]
    
    try:
        logger.info(f"Iniciando extração de médias para temporada {SEASON}")
        
        results = []
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
            except Exception as e:
                logger.error(f"✗ Erro na extração para {category}-{type_param}: {str(e)}", exc_info=True)
                results.append({"category": category, "type": type_param, "status": "error", "error": str(e)})
        
        # Resumo final
        logger.info("\n" + "="*60)
        logger.info("RESUMO DA EXTRAÇÃO")
        logger.info("="*60)
        for result in results:
            if result["status"] == "success":
                logger.info(f"✓ {result['category']}-{result['type']}: {result['path']}")
            else:
                logger.error(f"✗ {result['category']}-{result['type']}: {result.get('error', 'Erro desconhecido')}")
        logger.info("="*60)
        
        # Retorna 0 se todas as extrações foram bem-sucedidas
        if all(r["status"] == "success" for r in results):
            return 0
        else:
            return 1
            
    except Exception as e:
        logger.error(f"✗ Erro geral na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

