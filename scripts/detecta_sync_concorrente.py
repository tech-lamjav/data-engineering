"""Detector de sync concorrente: dois syncs do mesmo (sport, env) ao mesmo tempo (DE#107).

Sem argparse (regra .cursorrules). SOMENTE LEITURA (`gcloud logging read` e
`gcloud workflows executions list`), roda por mão humana, nunca por workflow, e não conserta
nada (ADR 0002, Decisão D). Precisa do `gcloud` autenticado; se pedir reauth, este script
NÃO tenta autenticar: sai com 2 e o diagnóstico vai para o stderr.

QUANDO RODAR
  - Depois do deploy da trava (workflow primeiro, imagem depois), nas janelas das 13 UTC:
    é quando o rebuild de `fact_odds_snapshot` passa de 900 s e, antes da trava, o retry do
    workflow rodava um segundo sync em cima do primeiro.
        DETECTA_DESDE=2026-10-03T12:30:00Z DETECTA_ATE=2026-10-03T14:30:00Z \\
            .venv/bin/python3 scripts/detecta_sync_concorrente.py
  - Para medir o "antes": a mesma coisa em 26 a 28/09/2026 deve achar as sobreposições.

VARIÁVEIS
  DETECTA_DESDE / DETECTA_ATE  janela em RFC 3339 UTC (default: as últimas 72 h).

CÓDIGO DE SAÍDA  0 = verde (nenhum par de syncs sobrepostos); 1 = vermelho; 2 = erro de
leitura (gcloud sem credencial, janela inválida). Ver src/monitoring/sync_concorrente.py
para as regras e os limites.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import BIGQUERY_LOCATION, BIGQUERY_PROJECT_ID, GCP_PROJECT_ID
from src.monitoring.sync_concorrente import (
    extrai_syncs,
    le_execucoes_workflow,
    le_logs,
    monta_relatorio,
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)
# O relatório é o stdout; o log do repo vai para o stderr para não se misturar.
for _h in logger.handlers:
    _h.setStream(sys.stderr)

JANELA_PADRAO = timedelta(hours=72)


def _instante(nome: str, padrao: datetime) -> datetime:
    bruto = os.getenv(nome)
    if not bruto:
        return padrao
    return datetime.fromisoformat(bruto.replace("Z", "+00:00")).astimezone(timezone.utc)


def main():
    try:
        agora = datetime.now(timezone.utc)
        ate = _instante("DETECTA_ATE", agora)
        desde = _instante("DETECTA_DESDE", ate - JANELA_PADRAO)
        if desde >= ate:
            logger.error("DETECTA_DESDE precisa ser anterior a DETECTA_ATE")
            return 2
        projeto = GCP_PROJECT_ID or BIGQUERY_PROJECT_ID

        logger.info(f"Janela {desde.isoformat()} .. {ate.isoformat()} (projeto {projeto})")
        resultado = extrai_syncs(le_logs(projeto, desde, ate))
        execs = le_execucoes_workflow(projeto, BIGQUERY_LOCATION, desde, ate)
        texto, codigo = monta_relatorio(resultado, execs)
        print(texto)
        return codigo
    except subprocess.CalledProcessError as e:
        # Credencial vencida ou sem permissão: declarar e parar (não reautenticar).
        logger.error(f"gcloud falhou (exit {e.returncode}): {(e.stderr or '').strip()[:500]}")
        return 2
    except Exception as e:
        logger.error(f"Erro: {str(e)}", exc_info=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
