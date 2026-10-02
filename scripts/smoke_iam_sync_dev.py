"""Smoke de IAM do sync (DEV e o cache de serving de PRD): a conta de runtime lê o BigQuery como o sync lê?

O nome do arquivo ficou da DE#106 (só DEV). Desde a DE#109 ele também prova a leitura filtrada
das odds em PRD (os dois query jobs do cache de serving) e é o passo 3 do runbook de PRD.

SOMENTE DRY-RUN (grátis, não lê linha, não escreve em Postgres). Sem argparse (regra
.cursorrules): configuração por variável de ambiente.

QUANDO RODAR
  Antes de deployar a imagem do sync (DE#106, DE#109), depois de conceder `roles/bigquery.jobUser` e
  a leitura dos datasets à conta de runtime DEDICADA ao sync (`sync-bq-postgres@`, história 51 da
  #112: a conta compartilhada pelos 29 serviços não ganha privilégio). Sem o smoke verde, não siga com o deploy.
      SMOKE_SERVICE_ACCOUNT=sync-bq-postgres@smartbetting-dados.iam.gserviceaccount.com \\
          .venv/bin/python3 scripts/smoke_iam_sync_dev.py

VARIÁVEIS
  SMOKE_SERVICE_ACCOUNT  e-mail da conta de runtime a impersonar. Quem roda precisa de
                         `roles/iam.serviceAccountTokenCreator` sobre ela e de ADC válido
                         (`gcloud auth application-default login`). Sem a variável, usa o ADC
                         como está, o que NÃO prova a conta de runtime: diga isso no relatório.

CÓDIGO DE SAÍDA  0 = verde; 1 = falta permissão (a mensagem diz qual tabela ou o pré-voo);
2 = erro de credencial ou de impersonation (não é veredito sobre a permissão da conta).
Ver src/monitoring/smoke_iam_sync.py.

O pré-voo do sync, que o smoke reaproveita, loga "não consegue criar query jobs" mesmo quando
a causa é a credencial (exit 2). Vale a última linha do `logger.error` do script e o código.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google.auth.exceptions import GoogleAuthError

from src.monitoring.smoke_iam_sync import cliente_bigquery, codigo_de_saida, executa_smoke
from src.utils.logger import setup_logger

logger = setup_logger(__name__)
# O relatório é o stdout; o log do repo vai para o stderr para não se misturar.
for _h in logger.handlers:
    _h.setStream(sys.stderr)


def main():
    conta = os.getenv("SMOKE_SERVICE_ACCOUNT") or None
    try:
        if conta is None:
            logger.warning(
                "SMOKE_SERVICE_ACCOUNT vazio: rodando com o ADC de quem executa, "
                "o que não prova a conta de runtime do sync"
            )
        bq = cliente_bigquery(conta)
        conferidas, falhas = executa_smoke(bq)
        for falha in falhas:
            print(f"FALHA {falha}")
        codigo = codigo_de_saida(conferidas, falhas)
        print(
            f"{'VERDE' if codigo == 0 else 'VERMELHO'}: {conferidas} leitura(s) filtrada(s) "
            f"conferida(s) em dry-run (tabelas de DEV com regra e o cache de serving de PRD), "
            f"{len(falhas)} falha(s) "
            f"(conta: {conta or 'ADC de quem executa'})"
        )
        return codigo
    except GoogleAuthError as e:
        logger.error(f"Credencial ou impersonation falhou ({type(e).__name__}): {e}")
        return 2
    except Exception as e:
        logger.error(f"Erro: {e}", exc_info=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
