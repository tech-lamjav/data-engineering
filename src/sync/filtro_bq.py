"""Leitura filtrada de uma tabela do BigQuery, para o sync (DE#106, reaproveitada pela DE#109).

O sync lia `bq.list_rows()` (tabledata.list, gratuito, sem query job) e descartava linhas em
Python: para gravar ~698 mil linhas de odds no DEV, lia ~4,16 mi. Aqui o corte roda no
BigQuery, por query job parametrizado, e só as linhas que entram chegam ao processo.

O módulo é genérico de propósito. Um filtro é uma cláusula SQL com parâmetros nomeados
(`FiltroBQ`); quem o monta decide a regra:
- a retenção de DEV (DE#106) monta o dela em `src.sync.retencao`/`bq_to_postgres`
  (corte de coleta, temporada, lista de `fixture_id` elegíveis);
- a DE#109 compõe o dela com as mesmas peças (`e`/`ou`): mercados servidos, corte de partição
  e a janela de fechamento das fixtures antigas.

PRÉ-REQUISITO DE IAM: query job exige `bigquery.jobs.create` na conta de runtime do sync. O
`list_rows` não exigia. A falta da permissão levanta 403 e o sync aborta com erro explícito (como
a falha de IAM já faz): a retenção nunca degrada em silêncio para "sem filtro". Ver o runbook
de deploy do PR da DE#106.

Segurança: valores entram SEMPRE como parâmetro de query. Só nomes de tabela e de coluna entram
como texto, e passam por uma checagem de identificador (a allowlist do sync já os restringe; a
checagem torna isso explícito e protege quem reaproveitar o módulo).

Sem dependência de banco: só o cliente do BigQuery, que o chamador injeta.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from google.cloud import bigquery

_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABELA = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _identificador(nome: str) -> str:
    if not _IDENTIFICADOR.match(nome or ""):
        raise ValueError(f"identificador de coluna inválido: {nome!r}")
    return f"`{nome}`"


def _referencia_de_tabela(table_ref: str) -> str:
    if not _TABELA.match(table_ref or ""):
        raise ValueError(f"referência de tabela inválida: {table_ref!r}")
    return f"`{table_ref}`"


def _escalar(nome: str, valor) -> bigquery.ScalarQueryParameter:
    """Parâmetro escalar tipado. `datetime` antes de `date`: datetime é subclasse de date."""
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            raise ValueError(
                f"parâmetro {nome!r}: instante sem fuso. O BigQuery tratá-lo-ia como UTC e "
                f"um corte calculado em hora local ficaria deslocado em silêncio."
            )
        return bigquery.ScalarQueryParameter(nome, "TIMESTAMP", valor)
    if isinstance(valor, date):
        return bigquery.ScalarQueryParameter(nome, "DATE", valor)
    if isinstance(valor, bool):
        return bigquery.ScalarQueryParameter(nome, "BOOL", valor)
    if isinstance(valor, int):
        return bigquery.ScalarQueryParameter(nome, "INT64", valor)
    if isinstance(valor, float):
        return bigquery.ScalarQueryParameter(nome, "FLOAT64", valor)
    if isinstance(valor, str):
        return bigquery.ScalarQueryParameter(nome, "STRING", valor)
    raise ValueError(f"parâmetro {nome!r}: tipo sem mapeamento ({type(valor).__name__})")


@dataclass(frozen=True)
class FiltroBQ:
    """Cláusula WHERE (sem a palavra WHERE) + os parâmetros nomeados que ela referencia.

    Componha com `e`/`ou`. Os nomes dos parâmetros são de quem monta: um nome repetido na
    composição levanta ValueError (dois parâmetros diferentes com o mesmo nome fariam o
    BigQuery usar um deles em silêncio).
    """

    clausula: str
    parametros: tuple = ()

    @classmethod
    def desde(cls, coluna: str, valor, nome: str) -> "FiltroBQ":
        """`coluna >= @nome`. Valor NULL na coluna nunca passa (NULL >= x não é verdadeiro)."""
        return cls(f"{_identificador(coluna)} >= @{nome}", (_escalar(nome, valor),))

    @classmethod
    def igual(cls, coluna: str, valor, nome: str) -> "FiltroBQ":
        """`coluna = @nome`."""
        return cls(f"{_identificador(coluna)} = @{nome}", (_escalar(nome, valor),))

    @classmethod
    def em_lista(cls, coluna: str, valores: Iterable[int], nome: str) -> "FiltroBQ":
        """`coluna IN UNNEST(@nome)` com array de INT64 (vazio continua sendo array tipado)."""
        lista = [int(v) for v in valores]
        return cls(
            f"{_identificador(coluna)} IN UNNEST(@{nome})",
            (bigquery.ArrayQueryParameter(nome, "INT64", lista),),
        )

    def _combina(self, outro: "FiltroBQ", operador: str) -> "FiltroBQ":
        repetidos = {p.name for p in self.parametros} & {p.name for p in outro.parametros}
        if repetidos:
            raise ValueError(f"parâmetros repetidos na composição do filtro: {sorted(repetidos)}")
        return FiltroBQ(
            f"({self.clausula}) {operador} ({outro.clausula})",
            self.parametros + outro.parametros,
        )

    def e(self, outro: "FiltroBQ") -> "FiltroBQ":
        return self._combina(outro, "AND")

    def ou(self, outro: "FiltroBQ") -> "FiltroBQ":
        return self._combina(outro, "OR")


def le_tabela_filtrada(
    bq: bigquery.Client,
    table_ref: str,
    colunas: Sequence[str],
    filtro: FiltroBQ,
    maximo_bytes_faturados: Optional[int] = None,
):
    """Roda `SELECT colunas FROM table_ref WHERE filtro` e devolve o iterador de linhas.

    Submete o job e ESPERA o resultado (`result()`) antes de voltar: quem chama o faz antes de
    qualquer TRUNCATE, então 403 (sem `bigquery.jobs.create`) ou estouro do teto de bytes
    levantam com a tabela de destino ainda intacta. As linhas vêm paginadas do resultado do
    job (leitura gratuita) e não são tocadas aqui.

    `maximo_bytes_faturados`: teto do job; acima dele o BigQuery recusa a query sem cobrar.
    """
    lista = ", ".join(_identificador(c) for c in colunas)
    sql = f"SELECT {lista} FROM {_referencia_de_tabela(table_ref)} WHERE {filtro.clausula}"
    job_config = bigquery.QueryJobConfig(query_parameters=list(filtro.parametros))
    # Só atribui quando pedido: a lib serializa None como a string 'None' e o getter quebra.
    if maximo_bytes_faturados is not None:
        job_config.maximum_bytes_billed = maximo_bytes_faturados
    return bq.query(sql, job_config=job_config).result()
