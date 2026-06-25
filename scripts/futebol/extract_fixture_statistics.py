"""Script de extração de /fixtures/statistics da API-Football (stats por time/jogo).

Sem argparse (regra `.cursorrules`). Modo via env var `FIXTURE_STATISTICS_MODE`:
- current (default): ano corrente — schedule diário (janela 3d + skip-if-exists)
- backfill: anos anteriores — one-shot manual (skip-if-exists)
"""
import sys
from pathlib import Path

# scripts/futebol/extract_fixture_statistics.py → sobe 3 níveis até data-engineering/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.fixture_statistics_extractor import FixtureStatisticsExtractor
from src.config import get_mode
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        mode = get_mode("FIXTURE_STATISTICS_MODE")
        logger.info(f"Iniciando extract_fixture_statistics (mode={mode})")
        paths = FixtureStatisticsExtractor(mode=mode).extract_and_save()
        logger.info(f"✓ Concluído: {len(paths)} arquivo(s) salvo(s)")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
