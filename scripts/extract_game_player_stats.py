"""Script individual para extração de estatísticas de jogadores por jogo."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.game_player_stats_extractor import GamePlayerStatsExtractor
from src.config import get_seasons_to_process
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# BACKFILL_SEASONS truthy → itera todas as seasons (uso pontual/backfill)
# Sem a var → usa apenas SEASON do .env (comportamento padrão para Cloud Run)
_seasons = get_seasons_to_process()


def main():
    try:
        all_paths = []
        for season in _seasons:
            logger.info(f"Iniciando extração de estatísticas para temporada {season}")
            gcs_paths = GamePlayerStatsExtractor(season=season).extract_and_save()
            all_paths.extend(gcs_paths)
            logger.info(f"Temporada {season}: {len(gcs_paths)} arquivo(s) salvo(s)")

        logger.info(f"Extração concluída: {len(all_paths)} arquivo(s) no total")
        return 0
    except Exception as e:
        logger.error(f"Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

