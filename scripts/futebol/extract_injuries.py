"""Script de extração de /injuries da API-Football (lesionados/suspensos).

Sem argparse (regra `.cursorrules`). Modo via env var `INJURIES_MODE`:
- current (default): ano corrente — snapshot diário (schedule)
- backfill: anos anteriores — one-shot manual
"""
import os
import sys
from pathlib import Path

# scripts/futebol/extract_injuries.py → sobe 3 níveis até data-engineering/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.injuries_extractor import InjuriesExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        mode = os.getenv("INJURIES_MODE", "current")
        logger.info(f"Iniciando extract_injuries (mode={mode})")
        gcs_path = InjuriesExtractor(mode=mode).extract_and_save()
        logger.info(f"✓ Concluído: {gcs_path}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
