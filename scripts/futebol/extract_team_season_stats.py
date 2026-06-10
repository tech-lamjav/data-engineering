"""Script de extração de /teams/statistics da API-Football (agregados de temporada).

Sem argparse (regra `.cursorrules`). Modo via env var `TEAM_SEASON_STATS_MODE`:
- current (default): ano corrente — schedule semanal
- backfill: anos anteriores — one-shot manual
"""
import os
import sys
from pathlib import Path

# scripts/futebol/extract_team_season_stats.py → sobe 3 níveis até data-engineering/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.team_season_stats_extractor import TeamSeasonStatsExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        mode = os.getenv("TEAM_SEASON_STATS_MODE", "current")
        logger.info(f"Iniciando extract_team_season_stats (mode={mode})")
        gcs_path = TeamSeasonStatsExtractor(mode=mode).extract_and_save()
        logger.info(f"✓ Concluído: {gcs_path}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
