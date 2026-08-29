"""Script do detector de atraso do sync BQ -> Postgres.

Sem argparse (regra .cursorrules). Roda de hora em hora no GitHub Actions; manda e-mail
só quando o estado muda. Ver src/monitoring/atraso_sync.py para o porquê de cada escolha.

Teste local (sem enviar e-mail e sem gravar estado):
    DETECTOR_DRY_RUN=1 .venv/bin/python3 scripts/detector_atraso_sync.py

CÓDIGO DE SAÍDA — 1 só nos ciclos que FALAM (transição ou lembrete), não em todo ciclo
vermelho. Falhar de hora em hora enquanto o problema dura reintroduziria pela porta dos
fundos exatamente o que o ADR 0002 recusa: o GitHub notifica por execução, então um
vermelho de 3 dias viraria ~72 e-mails para o último committer do workflow — o mesmo papel
de parede que fez este incidente durar. Assim, um episódio produz no máximo um job vermelho
por dia, e a aba Actions continua registrando o momento em que algo mudou. Erro de execução
(exit 2) é sempre vermelho: aí o detector não mediu nada e o silêncio seria cego.
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
        # Vermelho já conhecido e já avisado: o e-mail não saiu neste ciclo, então o job
        # também não vira vermelho. Ver o bloco CÓDIGO DE SAÍDA no topo.
        return 1 if resultado["motivos"] else 0

    logger.info("Sync acompanhando o BigQuery nos dois ambientes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
