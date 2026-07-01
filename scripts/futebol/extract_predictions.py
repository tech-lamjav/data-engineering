"""Script de extração de /predictions da API-Football (previsão pré-jogo — baseline).

Sem argparse (regra `.cursorrules`). Sem modo: uma passada cobre as janelas de
FUTEBOL_PREDICTIONS_WINDOWS ("daily" = varre jogos NS futuros até 14d, 1x/dia via
date-stamp; "t2h" = refresh perto do jogo) — o extractor bucketa cada jogo NS pela
proximidade do kickoff. Forward-only. Rodado pelo poll workflow_futebol_predictions.yml
(Cloud Scheduler ~15min).
"""
import sys
from pathlib import Path

# scripts/futebol/extract_predictions.py → sobe 3 níveis até data-engineering/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.extractors.predictions_extractor import PredictionsExtractor
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        logger.info("Iniciando extract_predictions (janelas daily + t2h, jogos NS futuros)")
        paths = PredictionsExtractor().extract_and_save()
        logger.info(f"✓ Concluído: {len(paths)} snapshot(s) de previsão salvo(s)")
        return 0
    except Exception as e:
        logger.error(f"✗ Erro na extração: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
