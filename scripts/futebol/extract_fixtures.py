"""Script de extração de /fixtures da API-Football (tabela mãe de jogos).

Sem argparse (regra `.cursorrules`). Modo via env var `FIXTURES_MODE`:
- current (default): ano corrente — schedule diário (atualiza status/placar)
- backfill: anos anteriores — one-shot manual
"""
import os
import sys
from pathlib import Path

# scripts/futebol/extract_fixtures.py → sobe 3 níveis até data-engineering/ (project root)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.fixtures_extractor import FixturesExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        mode = os.getenv("FIXTURES_MODE", "current")
        logger.info(f"Iniciando extract_fixtures (mode={mode})")
        gcs_path = FixturesExtractor(mode=mode).extract_and_save()
        logger.info(f"✓ Concluído: {gcs_path}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
