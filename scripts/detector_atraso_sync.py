"""Script do detector de atraso do sync BQ -> Postgres.

Sem argparse (regra .cursorrules). Roda de hora em hora no GitHub Actions; manda e-mail
só quando o estado muda. Ver src/monitoring/atraso_sync.py para o porquê de cada escolha.

Teste local (sem enviar e-mail e sem gravar estado):
    DETECTOR_DRY_RUN=1 .venv/bin/python3 scripts/detector_atraso_sync.py

Sai com 1 se houver atraso acima do limiar, para que a aba Actions também mostre vermelho
— o e-mail é o canal principal, mas o job verde com serving parado seria mentira.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.monitoring.atraso_sync import roda_detector
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def main():
    try:
        resultado = roda_detector()
    except Exception as e:
        logger.error(f"Erro: {str(e)}", exc_info=True)
        return 2

    if resultado["vermelho"]:
        logger.error(f"Atraso acima do limiar: {resultado['por_ambiente']}")
        return 1

    logger.info("Sync acompanhando o BigQuery nos dois ambientes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
