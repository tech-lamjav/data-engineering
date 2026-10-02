"""Congela no BigQuery o universo de odds anterior ao corte de retenção (DE#109, história 57).

DRY-RUN POR PADRÃO: só lê metadados e imprime o plano. Grava SÓ com --apply (ou SNAPSHOT_APPLY=1):

    .venv/bin/python3 scripts/snapshot_odds_pre_corte.py             # dry-run
    .venv/bin/python3 scripts/snapshot_odds_pre_corte.py --apply     # cria a tabela datada

Sem argparse (regra .cursorrules): o único argumento é `--apply`, conferido à mão; qualquer outro
falha. Rode ANTES de ligar o cache de serving em PRD (passo do wizard de cutover das odds): depois
do corte o dado completo continua no BigQuery, mas a janela `daily` é regravada pelo dbt e o
universo de hoje deixa de ser refazível. Ver src/monitoring/snapshot_odds.py.

CÓDIGO DE SAÍDA  0 = dry-run relatado ou snapshot criado; 1 = o destino do dia já existe;
2 = erro (credencial, origem inexistente, argumento desconhecido).
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google.cloud import bigquery

from src.config import BIGQUERY_PROJECT_ID
from src.monitoring.snapshot_odds import SnapshotJaExiste, executa, pediu_apply
from src.sync.alvo import resolve_alvo_sync
from src.sync.retencao import TABELA_ODDS
from src.utils.logger import setup_logger

logger = setup_logger(__name__)
for _h in logger.handlers:
    _h.setStream(sys.stderr)


def cliente_bigquery():
    return bigquery.Client(project=BIGQUERY_PROJECT_ID)


def hoje():
    return datetime.now(timezone.utc).date()


def main(argv=None, ambiente=None):
    argv = sys.argv[1:] if argv is None else argv
    ambiente = os.environ if ambiente is None else ambiente
    try:
        aplicar = pediu_apply(argv, ambiente)
        dataset, _, _ = resolve_alvo_sync("futebol")
        r = executa(cliente_bigquery(), BIGQUERY_PROJECT_ID, dataset, TABELA_ODDS, hoje(), aplicar)
    except SnapshotJaExiste as e:
        print(f"RECUSADO: {e}")
        return 1
    except Exception as e:
        logger.error(f"Erro: {e}", exc_info=True)
        return 2
    plano = (
        f"origem {r.origem} ({r.linhas_origem:,} linhas, {r.bytes_origem / 1e6:,.0f} MB) -> "
        f"destino {r.destino}"
    )
    if r.aplicado:
        print(f"CRIADO: {plano}")
        print("Guarde o nome do destino: é a tabela que os scripts de análise passam a ler.")
    else:
        print(f"DRY-RUN (nada gravado): {plano}")
        if r.destino_ja_existe:
            print("ATENÇÃO: o destino de hoje já existe; --apply seria recusado.")
        print("Para gravar: rode de novo com --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
