"""Linha de base de leituras de uma cópia congelada no Postgres (DE#112, história 43).

`int_futebol_odds_devig` saiu do sync (`src/sync/alvo.py`) e as cópias dela no Postgres
ficam congeladas por ~7 dias antes do `DROP`, que é DDL do app (ticket ao Victor). O que
autoriza o `DROP` é nenhum leitor ter aparecido: `seq_scan` e `idx_scan` de
`pg_stat_user_tables` iguais em duas leituras separadas por pelo menos 7 dias, a primeira
no dia do congelamento (= dia do deploy do sync sem o devig).

Por que contadores e não "quem leu": o Postgres não diz quem. Hoje a tabela tem ~14,5 mil
`seq_scan` sem dono e ~97 consultas ad hoc ainda sem atribuição; a linha de base existe
para separar "lido no passado" de "lido depois do congelamento".

Tudo aqui é puro, exceto `le_leitura`, que só executa um SELECT na view de estatísticas.
Sem dependência de psycopg: quem abre a conexão é `scripts/leituras_copia_devig.py`.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

# Duas leituras mais próximas que isto não concluem nada: cobrir uma semana inteira é o
# que pega a rotina semanal (o `fact_team_season_stats` tem cadência semanal; um leitor
# semanal ficaria invisível em 3 dias).
INTERVALO_MINIMO = timedelta(days=7)

_CONSULTA = """
SELECT now(), seq_scan, idx_scan, n_live_tup
FROM pg_stat_user_tables
WHERE schemaname = %s AND relname = %s
"""

_CAMPOS = ("env", "medido_em", "seq_scan", "idx_scan", "n_live_tup")


@dataclass(frozen=True)
class Leitura:
    env: str
    medido_em: datetime
    seq_scan: int
    idx_scan: int
    n_live_tup: int  # estimativa do autovacuum: sinal, não contagem exata


@dataclass(frozen=True)
class Comparacao:
    # 'estavel' | 'lida' | 'cedo' | 'invalida'
    veredito: str
    delta_seq_scan: int
    delta_idx_scan: int
    delta_n_live_tup: int
    intervalo: timedelta


def le_leitura(pg_conn, env: str, schema: str, tabela: str) -> Leitura:
    """Lê os contadores da tabela. Só SELECT.

    Sem linha, ou com `seq_scan` ou `n_live_tup` NULL (papel sem visibilidade das
    estatísticas), levanta LookupError: imprimir zeros pareceria "ninguém lê" quando na
    verdade não se mediu nada.

    `idx_scan` é a exceção: o pg_stat_user_tables o devolve NULL (e não 0) numa tabela sem
    nenhum índice, e a cópia congelada do de-vig não tem índice algum. NULL ali é "nenhuma
    leitura por índice é possível" e vale 0. Só se aceita quando os outros dois contadores
    existem; um papel sem visibilidade os devolve NULL todos juntos.
    """
    with pg_conn.cursor() as cur:
        cur.execute(_CONSULTA, (schema, tabela))
        row = cur.fetchone()
    if row is None or row[1] is None or row[3] is None:
        raise LookupError(
            f"{schema}.{tabela} sem estatísticas legíveis em pg_stat_user_tables "
            f"(env={env}): tabela ausente, em outro schema ou papel sem visibilidade"
        )
    momento, seq_scan, idx_scan, n_live_tup = row
    return Leitura(
        env=env,
        medido_em=momento,
        seq_scan=int(seq_scan),
        idx_scan=int(idx_scan or 0),
        n_live_tup=int(n_live_tup),
    )


def compara(base: Leitura, repeticao: Leitura) -> Comparacao:
    """O que a repetição diz em relação à linha de base.

    - 'invalida': não dá para comparar (ambientes diferentes, repetição anterior à base,
      ou contador que DIMINUIU — `pg_stat_reset` ou crash do servidor zeram as
      estatísticas, e "zero de diferença" viria dos contadores recomeçando).
    - 'cedo': menos de 7 dias entre as leituras; não conclui.
    - 'lida': `seq_scan` ou `idx_scan` subiu; alguém leu a cópia depois do congelamento.
    - 'estavel': os dois contadores iguais depois de 7 dias ou mais.
    """
    intervalo = repeticao.medido_em - base.medido_em
    d_seq = repeticao.seq_scan - base.seq_scan
    d_idx = repeticao.idx_scan - base.idx_scan
    d_linhas = repeticao.n_live_tup - base.n_live_tup

    if base.env != repeticao.env or intervalo < timedelta(0) or d_seq < 0 or d_idx < 0:
        veredito = "invalida"
    elif d_seq > 0 or d_idx > 0:
        veredito = "lida"
    elif intervalo < INTERVALO_MINIMO:
        veredito = "cedo"
    else:
        veredito = "estavel"
    return Comparacao(veredito, d_seq, d_idx, d_linhas, intervalo)


def para_json(leitura: Leitura) -> str:
    """Uma linha de JSON, para colar no ticket e reler 7 dias depois."""
    return json.dumps(
        {
            "env": leitura.env,
            "medido_em": leitura.medido_em.isoformat(),
            "seq_scan": leitura.seq_scan,
            "idx_scan": leitura.idx_scan,
            "n_live_tup": leitura.n_live_tup,
        }
    )


def de_json(texto: str) -> Leitura:
    dados = json.loads(texto)
    faltam = [c for c in _CAMPOS if c not in dados]
    if faltam:
        raise ValueError(f"registro de leitura sem campo(s): {faltam}")
    return Leitura(
        env=dados["env"],
        medido_em=datetime.fromisoformat(dados["medido_em"]),
        seq_scan=int(dados["seq_scan"]),
        idx_scan=int(dados["idx_scan"]),
        n_live_tup=int(dados["n_live_tup"]),
    )


def indexa_por_env(texto: str) -> dict[str, Leitura]:
    """Registros (uma linha de JSON por ambiente) -> {env: Leitura}.

    Dois registros do mesmo ambiente levantam ValueError: não há como saber qual é a linha
    de base, e escolher uma em silêncio decidiria o `DROP` por sorteio.
    """
    por_env: dict[str, Leitura] = {}
    for linha in texto.splitlines():
        if not linha.strip():
            continue
        leitura = de_json(linha)
        if leitura.env in por_env:
            raise ValueError(f"mais de um registro para o ambiente {leitura.env!r}")
        por_env[leitura.env] = leitura
    return por_env
