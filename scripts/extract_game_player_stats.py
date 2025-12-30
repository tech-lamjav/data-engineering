"""Script individual para extração de estatísticas de jogadores por jogo."""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.game_player_stats_extractor import GamePlayerStatsExtractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Extrair estatísticas de jogadores por jogo")
    parser.add_argument(
        "--season",
        type=int,
        default=SEASON,
        help=f"Ano da temporada (default: {SEASON})",
    )
    parser.add_argument(
        "--game-ids",
        type=int,
        nargs="+",
        help="IDs dos jogos (opcional)",
    )
    parser.add_argument(
        "--player-ids",
        type=int,
        nargs="+",
        help="IDs dos jogadores (opcional)",
    )
    
    args = parser.parse_args()
    
    try:
        logger.info(f"Iniciando extração de estatísticas para temporada {args.season}")
        extractor = GamePlayerStatsExtractor(season=args.season)
        
        gcs_paths = extractor.extract_and_save(
            game_ids=args.game_ids,
            player_ids=args.player_ids,
        )
        
        if gcs_paths:
            logger.info(f"✓ Extração concluída: {len(gcs_paths)} arquivo(s) salvo(s)")
            for path in gcs_paths:
                logger.info(f"  → {path}")
        else:
            logger.warning("Nenhum arquivo foi salvo.")
        
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

