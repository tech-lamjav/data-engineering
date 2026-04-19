"""Script para extração de médias da temporada."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
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
        {"category": "tracking", "type": "passing"},
    ]
    season_types = ["regular", "playoffs", "ist"]

    try:
        logger.info(f"Iniciando extração de médias para temporada {SEASON}")

        results = []
        for season_type in season_types:
            for combo in combinations:
                category = combo["category"]
                type_param = combo["type"]

                logger.info(f"Processando: season_type={season_type}, category={category}, type={type_param}")

                try:
                    extractor = SeasonAveragesExtractor(
                        season=SEASON,
                        category=category,
                        type=type_param,
                        season_type=season_type,
                    )
                    gcs_path = extractor.extract_and_save()
                    logger.info(f"✓ Extração concluída para {season_type}-{category}-{type_param}: {gcs_path}")
                    results.append({"season_type": season_type, "category": category, "type": type_param, "path": gcs_path, "status": "success"})
                except requests.exceptions.HTTPError as e:
                    status_code = e.response.status_code if e.response is not None else None
                    if status_code in (400, 404, 422):
                        logger.warning(f"⚠ Sem dados para {season_type}-{category}-{type_param} (HTTP {status_code}): {str(e)}")
                        results.append({"season_type": season_type, "category": category, "type": type_param, "status": "skipped", "error": str(e)})
                    else:
                        logger.error(f"✗ Erro HTTP na extração para {season_type}-{category}-{type_param}: {str(e)}", exc_info=True)
                        results.append({"season_type": season_type, "category": category, "type": type_param, "status": "error", "error": str(e)})
                except Exception as e:
                    logger.error(f"✗ Erro na extração para {season_type}-{category}-{type_param}: {str(e)}", exc_info=True)
                    results.append({"season_type": season_type, "category": category, "type": type_param, "status": "error", "error": str(e)})
        
        # Resumo final
        logger.info("\n" + "="*60)
        logger.info("RESUMO DA EXTRAÇÃO")
        logger.info("="*60)
        for result in results:
            label = f"{result['season_type']}-{result['category']}-{result['type']}"
            if result["status"] == "success":
                logger.info(f"✓ {label}: {result['path']}")
            elif result["status"] == "skipped":
                logger.warning(f"⚠ {label}: sem dados disponíveis (ignorado)")
            else:
                logger.error(f"✗ {label}: {result.get('error', 'Erro desconhecido')}")
        logger.info("="*60)

        # Retorna 1 apenas se houver erros reais (não "skipped")
        if any(r["status"] == "error" for r in results):
            return 1
        return 0
            
    except Exception as e:
        logger.error(f"✗ Erro geral na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

