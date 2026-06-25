"""Script de extração de /standings da API-Football (tabela do campeonato).

Sem argparse (regra `.cursorrules`). Modo via env var `STANDINGS_MODE`:
- current (default): ano corrente — snapshot diário (schedule)
- backfill: anos anteriores (tabela final) — one-shot manual
"""
import os
import sys
from pathlib import Path

# scripts/futebol/extract_standings.py → sobe 3 níveis até data-engineering/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.standings_extractor import StandingsExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        mode = os.getenv("STANDINGS_MODE", "current")
        logger.info(f"Iniciando extract_standings (mode={mode})")
        gcs_path = StandingsExtractor(mode=mode).extract_and_save()
        if not gcs_path:
            logger.warning("extract_standings: extração retornou vazio (nada gravado).")
        else:
            logger.info(f"✓ Concluído: {gcs_path}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
