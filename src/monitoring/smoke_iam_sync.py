"""Smoke de IAM do sync do futebol: a retenção de DEV e o cache de serving de PRD (DE#106 e DE#109,
histórias 32 da #106 e 51 da #112).

A retenção de DEV (DE#106) e o cache de serving das odds em PRD (DE#109) leem as tabelas por query
job filtrado no BigQuery. Isso exige da conta de runtime do sync duas coisas que o `list_rows`
antigo não exigia: `bigquery.jobs.create`
(ex.: `roles/bigquery.jobUser` no projeto) e leitura do dataset. Sem a primeira, o sync aborta no
pré-voo; sem a segunda, ele falha na primeira tabela. A DE#109 quer essa permissão numa conta de
runtime DEDICADA ao sync (`sync-bq-postgres@`), para a conta compartilhada pelos 29 serviços não
ganhar privilégio: o smoke prova a conta dedicada (impersonada) ANTES do deploy. O smoke prova as duas ANTES do
deploy, sob a própria conta de runtime (impersonada), e só com dry-run: grátis, nenhuma linha
lida, nada escrito em Postgres.

Reaproveita o código do sync (o mesmo pré-voo, a mesma tradução da regra em filtro, a mesma
leitura filtrada com teto de bytes), de modo que "o smoke passou" significa "o sync consegue".
O que NÃO prova: o acesso aos secrets do Postgres e a conexão com o pooler.

Código de saída (ver `scripts/smoke_iam_sync_dev.py`): 0 verde; 1 falta permissão; 2 não foi
possível nem autenticar (credencial, impersonation).
"""
from datetime import datetime, timedelta, timezone

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
from src.sync.filtro_bq import FiltroBQ, le_tabela_filtrada
from src.sync.odds_serving import TABELA_ODDS
from src.sync.retencao import resolve_regra_retencao

SPORT = "futebol"
# O cache de serving de PRD, ligado só nas odds, como o workflow vai ligar.
CACHE_SERVING_PRD = frozenset({TABELA_ODDS})
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


def _confere_tabela(bq, simulado, dataset, tabela, regra, agora):
    """get_table (leitura do dataset) + dry-run da leitura filtrada que o sync faria."""
    ref = f"{BIGQUERY_PROJECT_ID}.{dataset}.{tabela}"
    bq_table = bq.get_table(ref)
    campos = list(bq_table.schema)
    colunas = [c.name for c in campos if not _is_complex_field(c)]
    teto = _teto_de_bytes_faturados(getattr(bq_table, "num_bytes", None))
    ids = []
    if regra["kind"] == "cache_serving":
        # Primeiro job do cache de serving (as fixtures elegíveis), com o mesmo filtro do sync.
        # Dry-run não devolve linha, então NÃO se usa `le_fixtures_elegiveis` (que recusa lista
        # vazia): o dry-run só prova a permissão e a validade da SQL.
        le_tabela_filtrada(
            simulado, f"{BIGQUERY_PROJECT_ID}.{dataset}.{regra['fixtures_table']}",
            [regra["column"]],
            FiltroBQ.desde(regra["kickoff_column"], agora - timedelta(days=regra["days"]), "corte_kickoff"),
            maximo_bytes_faturados=_teto_de_bytes_faturados(None),
        )
        ids = [0]
    # Dry-run não lê nada: a lista de fixtures elegíveis pode ser vazia.
    filtro = _filtro_da_regra(regra, campos, agora, ids)
    le_tabela_filtrada(simulado, ref, colunas, filtro, maximo_bytes_faturados=teto)


def executa_smoke(bq, agora: datetime | None = None) -> tuple[int, list[str]]:
    """(tabelas conferidas, falhas). Falha de permissão vira texto; erro de credencial sobe.

    1. Pré-voo do sync (`SELECT 1` em dry-run): prova `bigquery.jobs.create`. Falhou, para: sem
       essa permissão toda tabela falharia pelo mesmo motivo.
    2. Para cada tabela de DEV com regra de retenção: `get_table` (leitura do dataset) e o
       dry-run da leitura filtrada que o sync faria, com o filtro real da regra.
    3. O cache de serving de PRD (DE#109): o dry-run dos DOIS jobs das odds (as fixtures
       elegíveis e a leitura filtrada), com a regra e o filtro reais.
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
    alvos = []  # (rótulo, tabela, regra)
    for tabela in tabelas:
        regra = resolve_regra_retencao(SPORT, "dev", tabela)
        if regra is not None:
            alvos.append((f"{tabela} (DEV)" if tabela == TABELA_ODDS else tabela, tabela, regra))
    for tabela in tabelas:
        regra = resolve_regra_retencao(SPORT, "prd", tabela, CACHE_SERVING_PRD)
        if regra is not None:
            alvos.append((f"{tabela} (PRD, cache de serving)", tabela, regra))

    conferidas = 0
    falhas: list[str] = []
    for rotulo, tabela, regra in alvos:
        try:
            _confere_tabela(bq, simulado, dataset, tabela, regra, agora)
            conferidas += 1
        except GoogleAuthError:
            raise
        except Exception as e:
            falhas.append(f"{rotulo}: {type(e).__name__}: {e}")
    return conferidas, falhas


def codigo_de_saida(conferidas: int, falhas: list[str]) -> int:
    """0 só se conferiu alguma tabela e nada falhou; senão 1 (falta permissão)."""
    return 0 if conferidas > 0 and not falhas else 1
