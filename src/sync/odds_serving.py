"""O Postgres de PRD como cache de serving das odds (DE#109, ADR 0006, spec DE#112).

POR QUE EXISTE. `fact_odds_snapshot` é copiada inteira a cada sync (4,3 mi de linhas, ~0,9 GB) e
o rebuild das 13 UTC passa de 900 s. Em PRD só o que o app serve precisa estar no Postgres:

- só os MERCADOS SERVIDOS (`alvo.MERCADOS_SERVIDOS`, a constante única), no nível do mercado
  inteiro (filtro por linha acoplaria o sync ao corpo de uma RPC do app);
- fixtures FUTURAS e com kickoff nos últimos `RETENCAO_PRODUTO_DIAS_ATRAS` (30) dias entram com
  TODAS as janelas; as mais antigas ficam só com a janela de fechamento (T-15m), a linha do CLV e
  da "foto" de jogo passado. O corte usa o kickoff de `fact_fixtures`, não o das odds.

O FILTRO RODA NO BIGQUERY, reaproveitando `src.sync.filtro_bq` (DE#106): dois query jobs, ambos
submetidos e ESPERADOS antes de qualquer TRUNCATE (sem `bigquery.jobs.create` o sync aborta com a
tabela de destino intacta):
  1. as fixtures elegíveis (`fact_fixtures`, ~11 mil linhas, bytes desprezíveis);
  2. as odds: `market_id IN mercados AND (fixture_id IN elegíveis OR collection_window = 't15m')`.

POR QUE NÃO HÁ CORTE DE PARTIÇÃO (a história 35 da spec previa um). O ramo do fechamento lê as
partições TODAS (o T-15m de um jogo de 2026-06 está numa partição velha), e uma cláusula em OR
não poda partição: o job fatura a tabela inteira de qualquer jeito. Separar em dois jobs, um com
corte e outro sem, só faturaria MAIS (o sem-corte já lê tudo). O teto de bytes continua o de um
full scan, que a spec já assumia (~707 MB, ~R$ 0,022 por execução); o ganho é nas LINHAS que
chegam ao processo (~1,0 mi em vez de ~4,3 mi), que é o que dita o tempo do COPY. Medir bytes
reais, por query, é o smoke pós-deploy do PR.

A TABELA NÃO É APPEND-ONLY. A janela `daily` é recapturada (até 7 vezes por dia) e SOBRESCRITA,
com `collection_timestamp` e `collection_date` novos. Por isso o filtro NÃO usa marca-d'água nem
data de captura (nem corte de partição): decide por mercado, fixture elegível e janela, e a carga
é completa a cada vez (TRUNCATE ou troca + COPY). Carga incremental está fora de escopo (reabre
se a leitura filtrada passar de 300 s).

VERSÃO DA REGRA (história 36). Mudar a lista de mercados ou o corte de 30 dias precisa forçar
nova carga mesmo com o BigQuery inalterado (senão o skip-if-unchanged mantém a regra velha e o
`force` não é exposto no serviço). `regra_versao` descreve a regra EM VIGOR numa string
determinística, gravada junto do estado de sincronização (coluna `regra_versao`, SQL
administrativo em `scripts/sql/sync_state_regra_versao.sql`); o sync só pula a tabela se o BigQuery
não mudou E a versão gravada é a de agora. Desligar o cache em PRD (rollback do workflow) muda a
versão para "sem regra" e a tabela completa volta na carga seguinte.

Sem dependência de banco: só `src.sync.filtro_bq` (cliente do BigQuery injetado) e a config.
"""
from datetime import datetime, timedelta
from typing import Iterable

from src.sync.alvo import MERCADOS_SERVIDOS  # noqa: F401 (reexporta a constante única)
from src.sync.filtro_bq import FiltroBQ, le_tabela_filtrada
from src.sync.retencao import TABELA_ODDS  # a única tabela com versão de regra no estado

# Revisão manual da LÓGICA da regra: some 1 quando o formato do filtro mudar sem que mercados nem
# dias mudem (a versão derivada dos parâmetros não veria a diferença).
REVISAO_DA_REGRA = 1


def filtro_mercados(regra: dict) -> FiltroBQ:
    """`market_id IN UNNEST(@mercados)`: o nível do mercado inteiro, nunca da linha."""
    return FiltroBQ.em_lista(regra["market_column"], regra["market_ids"], "mercados")


def filtro_cache_serving(regra: dict, fixtures_elegiveis: Iterable[int]) -> FiltroBQ:
    """Filtro das odds de PRD: mercado servido E (fixture elegível OU janela de fechamento).

    Fixture elegível (futura ou dos últimos 30 dias) entra com todas as janelas; qualquer outra
    só com o fechamento. Lista vazia continua sendo array tipado e deixa SÓ o fechamento (nunca
    "tudo"): quem chama decide se uma lista vazia é plausível (`le_fixtures_elegiveis` recusa).
    """
    elegivel = FiltroBQ.em_lista(regra["column"], fixtures_elegiveis, "ids_elegiveis")
    fechamento = FiltroBQ.igual(regra["closing_column"], regra["closing_window"], "janela_fechamento")
    return filtro_mercados(regra).e(elegivel.ou(fechamento))


def le_fixtures_elegiveis(
    bq,
    fixtures_ref: str,
    regra: dict,
    agora: datetime,
    maximo_bytes_faturados: int | None = None,
) -> list[int]:
    """`fixture_id` das fixtures futuras ou com kickoff nos últimos `days` dias, do BigQuery.

    Submete e ESPERA o job (antes de qualquer TRUNCATE). Lista vazia ABORTA: `fact_fixtures` sem
    nenhuma fixture recente não é um estado real do produto (são ~11 mil linhas, e há jogos todo
    dia), e seguir com ela gravaria só o fechamento, cortando as odds de PRD em silêncio.
    """
    corte = agora - timedelta(days=regra["days"])
    filtro = FiltroBQ.desde(regra["kickoff_column"], corte, "corte_kickoff")
    linhas = le_tabela_filtrada(
        bq, fixtures_ref, [regra["column"]], filtro,
        maximo_bytes_faturados=maximo_bytes_faturados,
    )
    ids = sorted({int(linha[regra["column"]]) for linha in linhas if linha[regra["column"]] is not None})
    if not ids:
        raise RuntimeError(
            f"cache de serving das odds: nenhuma fixture elegível em {fixtures_ref} (kickoff >= "
            f"{corte.isoformat()}). Recusado ANTES do TRUNCATE: seguir gravaria só o fechamento "
            f"(T-15m) e cortaria o PRD em silêncio."
        )
    return ids


def regra_versao(regra: dict | None) -> str | None:
    """Descrição determinística da regra em vigor, ou None se a tabela não tem regra.

    Entra no estado de sincronização (coluna `regra_versao`). Muda quando muda QUALQUER coisa que
    muda o conteúdo da tabela: a lista de mercados, os dias de retenção, a janela de fechamento,
    o ambiente (a regra de DEV e a de PRD são diferentes) ou a revisão manual da lógica.
    """
    if regra is None:
        return None
    mercados = ",".join(str(m) for m in sorted(regra["market_ids"]))
    if regra["kind"] == "cache_serving":
        return (
            f"cache-serving/r{REVISAO_DA_REGRA}/mercados={mercados}/dias={regra['days']}"
            f"/fechamento={regra['closing_window']}"
        )
    return f"dev-coleta/r{REVISAO_DA_REGRA}/mercados={mercados}/dias={regra['days']}"


def valida_cache_serving(
    sport: str, env: str, cache_serving: frozenset, troca: frozenset, staged: frozenset,
    resolved: list[str],
) -> None:
    """Recusa, ANTES de conectar em qualquer coisa, uma seleção que o desenho proíbe.

    - `cache_serving` só vale para o futebol, só em PRD (em DEV o filtro de mercados já vale
      sempre, sem ligar nada) e só para as odds, que precisam estar na execução;
    - as odds só entram na carga por troca (ou staged) de PRD DEPOIS do filtro: a sombra completa
      custaria ~+920 MB de disco (ADR 0006). Em DEV o filtro já vale, então a troca é livre.
    """
    if cache_serving:
        if (sport or "").lower() != "futebol":
            raise ValueError(f"cache de serving só vale para o futebol (sport={sport!r})")
        if (env or "").lower() != "prd":
            raise ValueError(
                "cache de serving só se liga em PRD: em DEV o filtro de mercados já vale sempre "
                "(a regra de DEV é a retenção de coleta mais os mercados servidos)"
            )
        fora_das_odds = sorted(cache_serving - {TABELA_ODDS})
        if fora_das_odds:
            raise ValueError(
                f"cache de serving só existe para {TABELA_ODDS}; pedido para {fora_das_odds}"
            )
        ausentes = sorted(cache_serving - set(resolved))
        if ausentes:
            raise ValueError(f"cache de serving pedido para tabelas fora desta execução: {ausentes}")
    if (
        (env or "").lower() == "prd"
        and TABELA_ODDS in (troca | staged)
        and TABELA_ODDS not in cache_serving
    ):
        raise ValueError(
            f"{TABELA_ODDS} só entra na carga por troca de PRD depois do filtro do cache de "
            f"serving (ADR 0006): a sombra completa custaria ~+920 MB. Ligue o cache de serving "
            f"para a tabela na mesma execução. Para DESLIGAR o cache (rollback), tire "
            f"{TABELA_ODDS} de troca_prd e de staged_prd no MESMO YAML e no mesmo deploy de "
            f"workflows que zera cache_serving_prd: esta recusa vale para o sync de PRD inteiro, "
            f"não só para as odds."
        )
