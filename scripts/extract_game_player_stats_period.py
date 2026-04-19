"""Script individual para extração de estatísticas de jogadores por jogo por período."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from src.extractors.game_player_stats_period_extractor import GamePlayerStatsPeriodExtractor
from src.config import SEASON, SEASONS
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# BACKFILL_SEASONS=1 → itera todas as seasons (uso pontual/backfill)
# Sem a var → usa apenas SEASON do .env (comportamento padrão para Cloud Run)
_seasons = SEASONS if os.getenv("BACKFILL_SEASONS") else [SEASON]


def main():
    try:
        all_paths = []
        for season in _seasons:
            logger.info(f"Iniciando extração de estatísticas por período para temporada {season}")
            gcs_paths = GamePlayerStatsPeriodExtractor(season=season).extract_and_save()
            all_paths.extend(gcs_paths)
            logger.info(f"Temporada {season}: {len(gcs_paths)} arquivo(s) salvo(s)")

        logger.info(f"Extração concluída: {len(all_paths)} arquivo(s) no total")
        return 0
    except Exception as e:
        logger.error(f"Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
