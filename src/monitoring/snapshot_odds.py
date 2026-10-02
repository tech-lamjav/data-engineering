"""Snapshot congelado do universo de odds anterior ao corte (DE#109, história 57 da #112).

POR QUE EXISTE. O cache de serving corta `fact_odds_snapshot` no Postgres de PRD, mas o BigQuery
continua completo. Só que o BigQuery NÃO é imutável: a janela `daily` é recapturada (até 7 vezes
por dia) e o dbt a regrava por latest-wins a cada rebuild, e o dbt reconstrói a tabela inteira.
"Um número que ninguém consegue refazer não é um número": os ROI de escanteios e de handicap já
enviados só se refazem se alguém guardar a tabela como ela estava antes do corte. O Victor aceitou
apontar os scripts de análise do app para esta cópia (prop-play-predictor#542, 30/09/2026).

O QUE FAZ. Copia `<projeto>.<dataset>.fact_odds_snapshot` para uma tabela datada
`fact_odds_snapshot_pre_corte_AAAAMMDD` no mesmo dataset, por COPY JOB (grátis, preserva
particionamento e agrupamento, consistente num instante), com `WRITE_EMPTY`: NUNCA sobrescreve. Se o
destino do dia já existe, recusa. Carimba descrição e rótulo (`congelada=true`). BigQuery não tem
tabela somente leitura; a proteção é o nome, o rótulo e a descrição (e IAM, se alguém quiser).

DRY-RUN POR PADRÃO: só lê metadados (origem e destino) e relata o plano. Grava com `--apply` ou
`SNAPSHOT_APPLY=1`. Sem argparse (regra .cursorrules): o flag é conferido à mão e um argumento
desconhecido falha, para um typo não virar dry-run silencioso.

Só o cliente do BigQuery, injetado: o que é puro (nome, pedido de apply, plano) é testado sem nuvem.
"""
from dataclasses import dataclass
from datetime import date

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

SUFIXO_DO_SNAPSHOT = "pre_corte"


class SnapshotJaExiste(Exception):
    """O destino do dia já existe: o snapshot é congelado e nunca é sobrescrito."""


@dataclass(frozen=True)
class Relatorio:
    origem: str
    destino: str
    linhas_origem: int | None
    bytes_origem: int | None
    destino_ja_existe: bool
    aplicado: bool


def nome_do_snapshot(tabela: str, quando: date) -> str:
    """`fact_odds_snapshot` + 2026-10-02 -> `fact_odds_snapshot_pre_corte_20261002`."""
    return f"{tabela}_{SUFIXO_DO_SNAPSHOT}_{quando:%Y%m%d}"


def pediu_apply(argv: list[str], ambiente: dict) -> bool:
    """True só com `--apply` ou SNAPSHOT_APPLY=1. Qualquer outro argumento levanta ValueError."""
    desconhecidos = [a for a in argv if a != "--apply"]
    if desconhecidos:
        raise ValueError(
            f"argumento desconhecido: {desconhecidos[0]} (o único é --apply; sem ele é dry-run)"
        )
    return "--apply" in argv or ambiente.get("SNAPSHOT_APPLY") == "1"


def _existe(bq, ref: str) -> bool:
    try:
        bq.get_table(ref)
        return True
    except NotFound:
        return False


def executa(
    bq, projeto: str, dataset: str, tabela: str, quando: date, aplicar: bool
) -> Relatorio:
    """Planeja e, só com `aplicar`, congela. Origem inexistente levanta NotFound (também no dry-run)."""
    origem = f"{projeto}.{dataset}.{tabela}"
    destino = f"{projeto}.{dataset}.{nome_do_snapshot(tabela, quando)}"
    fonte = bq.get_table(origem)
    ja_existe = _existe(bq, destino)
    if ja_existe and aplicar:
        raise SnapshotJaExiste(
            f"{destino} já existe: o snapshot é congelado e nunca é sobrescrito. Se precisa de "
            f"outro, é outro dia (outro nome); apague este à mão só se tiver certeza."
        )
    relatorio = Relatorio(
        origem=origem, destino=destino, linhas_origem=fonte.num_rows,
        bytes_origem=fonte.num_bytes, destino_ja_existe=ja_existe, aplicado=False,
    )
    if not aplicar:
        return relatorio

    config = bigquery.CopyJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_EMPTY)
    bq.copy_table(origem, destino, job_config=config).result()
    copia = bq.get_table(destino)
    copia.description = (
        f"Snapshot CONGELADO de {origem} em {quando.isoformat()}, tirado antes do corte de "
        f"retenção do cache de serving de PRD (DE#109, ADR 0006). Só leitura por convenção: nunca "
        f"sobrescrever. Os scripts de análise de escanteios e handicap apontam para cá."
    )
    copia.labels = {"congelada": "true", "origem": tabela, "data": f"{quando:%Y%m%d}"}
    bq.update_table(copia, ["description", "labels"])
    return Relatorio(
        origem=origem, destino=destino, linhas_origem=fonte.num_rows,
        bytes_origem=fonte.num_bytes, destino_ja_existe=False, aplicado=True,
    )
