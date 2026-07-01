"""Script de extração de /injuries da API-Football (lesionados/suspensos).

Sem argparse (regra `.cursorrules`). Modo via env var `INJURIES_MODE`:
- current (default): ano corrente — snapshot season-log diário (schedule)
- backfill: anos anteriores — one-shot manual
- pregame: coleta FORWARD-ONLY por fixture dos jogos NS futuros (poll ~horário, S7)
"""
import sys
from pathlib import Path

# scripts/futebol/extract_injuries.py → sobe 3 níveis até data-engineering/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.injuries_extractor import InjuriesExtractor
from src.config import get_mode
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        mode = get_mode("INJURIES_MODE")
        logger.info(f"Iniciando extract_injuries (mode={mode})")
        result = InjuriesExtractor(mode=mode).extract_and_save()
        if mode == "pregame":
            # pregame retorna List[str] (1 path/jogo); current/backfill retornam str.
            logger.info(f"✓ Concluído: {len(result)} snapshot(s) pré-jogo salvo(s)")
        elif not result:
            logger.warning("extract_injuries: extração retornou vazio (nada gravado).")
        else:
            logger.info(f"✓ Concluído: {result}")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
