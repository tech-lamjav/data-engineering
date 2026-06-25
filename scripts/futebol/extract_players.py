"""Script de extração de /players da API-Football.

Sem argparse (regra `.cursorrules`). Modo via env var `PLAYERS_MODE`:
- current (default): ano corrente — schedule diário
- backfill: anos anteriores — one-shot manual
"""
import sys
from pathlib import Path

# scripts/futebol/extract_players.py → sobe 3 níveis até data-engineering/ (project root)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.players_extractor import PlayersExtractor
from src.config import get_mode
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        mode = get_mode("PLAYERS_MODE")
        logger.info(f"Iniciando extract_players (mode={mode})")
        gcs_path = PlayersExtractor(mode=mode).extract_and_save()
        logger.info(f"✓ Concluído: {gcs_path}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
