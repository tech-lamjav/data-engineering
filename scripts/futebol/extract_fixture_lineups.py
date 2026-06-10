"""Script de extração de /fixtures/lineups da API-Football (escalações por time/jogo).

Sem argparse (regra `.cursorrules`). Modo via env var `FIXTURE_LINEUPS_MODE`:
- current (default): pós-jogo ano corrente — schedule diário (janela 3d + skip-if-exists), grava _real
- backfill: pós-jogo anos anteriores — one-shot manual (skip-if-exists), grava _real
- pregame: pré-jogo (~T-30min) — poll dos jogos NS iminentes, grava _confirmed
"""
import os
import sys
from pathlib import Path

# scripts/futebol/extract_fixture_lineups.py → sobe 3 níveis até data-engineering/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.fixture_lineups_extractor import FixtureLineupsExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        mode = os.getenv("FIXTURE_LINEUPS_MODE", "current")
        logger.info(f"Iniciando extract_fixture_lineups (mode={mode})")
        paths = FixtureLineupsExtractor(mode=mode).extract_and_save()
        logger.info(f"✓ Concluído: {len(paths)} arquivo(s) salvo(s)")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
