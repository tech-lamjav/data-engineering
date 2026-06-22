"""Script para gerar e enviar o resumo diario de execucoes de workflows.

Sem argparse (regra .cursorrules). Cobre o dia calendario anterior em
America/Sao_Paulo e manda 1 email consolidado.

Teste local (sem enviar email): SUMMARY_DRY_RUN=1 python scripts/daily_summary.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reporting.daily_summary import run_daily_summary
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        result = run_daily_summary()
        logger.info(f"Resumo diario concluido: {result['date']} ({result['totals']})")
        return 0
    except Exception as e:
        logger.error(f"Erro: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
