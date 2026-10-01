"""Smoke de IAM do sync de DEV do futebol (DE#106, histórias 32 da #106 e 51 da #112).

A retenção de DEV lê as tabelas por query job filtrado no BigQuery. Isso exige da conta de
runtime do sync duas coisas que o `list_rows` antigo não exigia: `bigquery.jobs.create`
(ex.: `roles/bigquery.jobUser` no projeto) e leitura do dataset. Sem a primeira, o sync de DEV
aborta no pré-voo; sem a segunda, ele falha na primeira tabela. O smoke prova as duas ANTES do
deploy, sob a própria conta de runtime (impersonada), e só com dry-run: grátis, nenhuma linha
lida, nada escrito em Postgres.

Reaproveita o código do sync (o mesmo pré-voo, a mesma tradução da regra em filtro, a mesma
leitura filtrada com teto de bytes), de modo que "o smoke passou" significa "o sync consegue".
O que NÃO prova: o acesso aos secrets do Postgres e a conexão com o pooler.

Código de saída (ver `scripts/smoke_iam_sync_dev.py`): 0 verde; 1 falta permissão; 2 não foi
possível nem autenticar (credencial, impersonation).
"""
from datetime import datetime, timezone

import google.auth
from google.auth import impersonated_credentials
from google.auth.exceptions import GoogleAuthError
from google.cloud import bigquery

from src.config import BIGQUERY_PROJECT_ID
from src.sync.alvo import resolve_alvo_sync
from src.sync.bq_to_postgres import (
    _filtro_da_regra,
    _is_complex_field,
    _teto_de_bytes_faturados,
    _verifica_query_job,
)
from src.sync.filtro_bq import le_tabela_filtrada
from src.sync.retencao import resolve_regra_retencao

SPORT = "futebol"
ENV = "dev"
_ESCOPO = ["https://www.googleapis.com/auth/cloud-platform"]


class _ClienteDryRun:
    """Mesma interface de `query` do cliente BigQuery, mas todo job vira dry-run."""

    def __init__(self, bq):
        self._bq = bq

    def query(self, sql, job_config=None):
        job_config = job_config or bigquery.QueryJobConfig()
        job_config.dry_run = True
        return self._bq.query(sql, job_config=job_config)


def cliente_bigquery(service_account: str | None, projeto: str = BIGQUERY_PROJECT_ID):
    """Cliente BigQuery como o sync cria; com `service_account`, impersona essa conta.

    A impersonation parte das credenciais padrão (ADC) de quem roda, que precisa de
    `roles/iam.serviceAccountTokenCreator` sobre a conta. Sem `service_account`, usa o ADC como
    está (o que o Cloud Run faz em produção).
    """
    if not service_account:
        return bigquery.Client(project=projeto)
    origem, _ = google.auth.default()
    credenciais = impersonated_credentials.Credentials(
        source_credentials=origem,
        target_principal=service_account,
        target_scopes=_ESCOPO,
    )
    return bigquery.Client(project=projeto, credentials=credenciais)


def executa_smoke(bq, agora: datetime | None = None) -> tuple[int, list[str]]:
    """(tabelas conferidas, falhas). Falha de permissão vira texto; erro de credencial sobe.

    1. Pré-voo do sync (`SELECT 1` em dry-run): prova `bigquery.jobs.create`. Falhou, para: sem
       essa permissão toda tabela falharia pelo mesmo motivo.
    2. Para cada tabela de DEV com regra de retenção: `get_table` (leitura do dataset) e o
       dry-run da leitura filtrada que o sync faria, com o filtro real da regra.
    """
    agora = agora or datetime.now(timezone.utc)
    try:
        _verifica_query_job(bq)
    except GoogleAuthError:
        raise
    except Exception as e:
        return 0, [
            f"pré-voo: a conta não cria query jobs (bigquery.jobs.create, "
            f"ex.: roles/bigquery.jobUser no projeto): {type(e).__name__}: {e}"
        ]

    dataset, _, tabelas = resolve_alvo_sync(SPORT)
    simulado = _ClienteDryRun(bq)
    conferidas = 0
    falhas: list[str] = []
    for tabela in tabelas:
        regra = resolve_regra_retencao(SPORT, ENV, tabela)
        if regra is None:
            continue
        ref = f"{BIGQUERY_PROJECT_ID}.{dataset}.{tabela}"
        try:
            bq_table = bq.get_table(ref)
            campos = list(bq_table.schema)
            colunas = [c.name for c in campos if not _is_complex_field(c)]
            # Dry-run não lê nada: a lista de fixtures elegíveis pode ser vazia.
            filtro = _filtro_da_regra(regra, campos, agora, [])
            le_tabela_filtrada(
                simulado, ref, colunas, filtro,
                maximo_bytes_faturados=_teto_de_bytes_faturados(
                    getattr(bq_table, "num_bytes", None)
                ),
            )
            conferidas += 1
        except GoogleAuthError:
            raise
        except Exception as e:
            falhas.append(f"{tabela}: {type(e).__name__}: {e}")
    return conferidas, falhas


def codigo_de_saida(conferidas: int, falhas: list[str]) -> int:
    """0 só se conferiu alguma tabela e nada falhou; senão 1 (falta permissão)."""
    return 0 if conferidas > 0 and not falhas else 1
