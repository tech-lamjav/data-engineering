"""Script para extração de médias da temporada por time."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from src.extractors.team_season_averages_extractor import TeamSeasonAveragesExtractor
from src.config import SEASON, TEAM_SEASON_AVERAGES_COMBINATIONS, SEASON_TYPES, is_http_no_data
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    """
    Extrai médias da temporada por time para múltiplas combinações de category/type.
    Gera um arquivo por combinação+season_type em:
    nba/team_season_averages/{season}/raw_nba_team_season_averages_{season}-{category}-{type}-{season_type}.json
    """
    try:
        logger.info(f"Iniciando extração de team season averages para temporada {SEASON}")

        results = []
        for combo in TEAM_SEASON_AVERAGES_COMBINATIONS:
            category = combo["category"]
            type_ = combo["type"]
            for season_type in SEASON_TYPES:
                key = f"{category}/{type_}/{season_type}"
                logger.info(f"Processando: {key}")
                try:
                    extractor = TeamSeasonAveragesExtractor(
                        season=SEASON,
                        category=category,
                        type=type_,
                        season_type=season_type,
                    )
                    gcs_path = extractor.extract_and_save()
                    logger.info(f"✓ Extração concluída ({key}): {gcs_path}")
                    results.append({"key": key, "status": "success"})
                except requests.exceptions.HTTPError as e:
                    status_code = e.response.status_code if e.response is not None else None
                    if is_http_no_data(e):
                        logger.warning(f"⚠ Sem dados para {key} (HTTP {status_code}) — ignorado")
                        results.append({"key": key, "status": "skipped"})
                    else:
                        logger.error(f"✗ Erro HTTP na extração ({key}): {str(e)}", exc_info=True)
                        results.append({"key": key, "status": "error"})
                except Exception as e:
                    logger.error(f"✗ Erro na extração ({key}): {str(e)}", exc_info=True)
                    results.append({"key": key, "status": "error"})

        return 1 if any(r["status"] == "error" for r in results) else 0

    except Exception as e:
        logger.error(f"✗ Erro geral na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
