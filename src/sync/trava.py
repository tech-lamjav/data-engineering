"""Trava de sync concorrente por (sport, env) e timeouts do sync (DE#107).

POR QUE EXISTE. Dois syncs do mesmo (sport, env) gravavam ao mesmo tempo no mesmo
Postgres, com TRUNCATE + COPY das mesmas tabelas, todo dia desde 01/09 (PRD e DEV): o COPY de
`fact_odds_snapshot` rodava duas vezes, o lock do TRUNCATE dobrava, o DEV ganhava um pico de
disco e, em 27/09, dois COPY simultâneos estouraram os 2 GiB (OOM). A causa: um sync passa de
900 s, o Cloud Run corta a REQUISIÇÃO (504) mas a thread do handler continua, e o `http.get`
do workflow repete a chamada ~5 s depois, na mesma instância. O `--max-instances 1` do deploy
não serializa nada: `containerConcurrency=80` deixa a mesma instância aceitar várias
requisições. A trava é o mecanismo de serialização de verdade.

COMO FUNCIONA. `pg_try_advisory_lock` de SESSÃO, na conexão de destino, logo depois do
connect. O lock mora no Postgres, e não em memória, porque a instância muda a cada hora e
depois de OOM. Por que de sessão e não de transação (`_xact`): o sync faz commit a cada
tabela; um lock de transação soltaria no primeiro commit. Se a instância morrer, a conexão
cai (EOF), o Postgres faz rollback e solta o lock. O `pg_advisory_unlock` explícito no
`finally` é barato e não depende do pooler.

PREMISSA DO POOLER (sonda de 28/09 na DE#107, Supabase DEV). O Shared Pooler em modo sessão
(5432) prende o backend ao cliente enquanto ele estiver conectado, e ao devolver o backend ao
pool limpa o estado da sessão: um lock que não foi solto (instância morta) NÃO vaza para o
cliente seguinte (B recebeu o mesmo backend e conseguiu pegar o mesmo lock). Em modo transação
(6543) o desenho quebra: o lock ficaria num backend que outro cliente reaproveita no meio do
sync. Por isso `verifica_pooler_de_sessao` recusa a porta 6543 antes de conectar.
"""
from urllib.parse import urlparse

# Status que `run_sync` devolve quando a trava de (sport, env) já está com outro sync. O
# handler traduz para HTTP 409; o workflow trata 409 como "em andamento".
STATUS_OCUPADO = "busy"

# Pooler do Supabase em modo transação: não serve para lock de sessão (nem para COPY).
PORTA_POOLER_TRANSACAO = 6543

# O statement_timeout da SESSÃO do sync acompanha o timeout do Cloud Run (3600 s). O COPY de
# `fact_odds_snapshot` já teve máximo de 862 s nos logs de PRD e cresce ~6,5-9 s/dia; com o
# timeout antigo (900 s) ele estouraria em semanas, e o timeout maior do Cloud Run só vale
# se o do Postgres subir junto (DE#112, fatia 1). INVARIANTE: statement_timeout <= timeout do
# Cloud Run em `scripts/deploy_cloud_run.sh` (3600 s). Constante única: o `SET` e o teste leem
# daqui.
SYNC_STATEMENT_TIMEOUT_S = 3600

_SQL_TENTA = "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))"
_SQL_SOLTA = "SELECT pg_advisory_unlock(hashtextextended(%s, 0))"


def chave_trava(sport: str, env: str) -> str:
    """Texto que vira a chave (bigint) do advisory lock: uma por (sport, env).

    O hash é feito pelo Postgres (`hashtextextended`), o mesmo da sonda de 28/09.
    """
    return f"sync-bq-to-postgres:{sport}:{env}"


def verifica_pooler_de_sessao(pg_url: str) -> None:
    """Recusa a URL do pooler em modo transação (6543): o lock de sessão não vale nela."""
    try:
        porta = urlparse(pg_url).port
    except ValueError:
        # URL sem porta numérica válida: deixa o psycopg dar o erro de conexão de sempre.
        return
    if porta == PORTA_POOLER_TRANSACAO:
        raise RuntimeError(
            f"A URL do Postgres usa a porta {PORTA_POOLER_TRANSACAO} (Shared Pooler em modo "
            "transação). A trava de sync (advisory lock de sessão) e o COPY exigem o modo "
            "sessão (5432)."
        )


def tenta_trava(pg_conn, sport: str, env: str) -> bool:
    """Tenta pegar a trava de sessão de (sport, env). False = outro sync está com ela.

    Termina a transação implícita (o SELECT abre uma), mas o lock de sessão sobrevive a
    commit e a rollback: é isso que o distingue do `_xact`.
    """
    with pg_conn.cursor() as cur:
        cur.execute(_SQL_TENTA, (chave_trava(sport, env),))
        obtida = bool(cur.fetchone()[0])
    pg_conn.commit()
    return obtida


def solta_trava(pg_conn, sport: str, env: str) -> None:
    """Solta a trava de (sport, env). Nunca levanta: a conexão pode já estar morta.

    Se o unlock não acontecer, fechar a conexão solta o lock de sessão do mesmo jeito.
    """
    try:
        # Uma transação abortada pelo erro do sync precisa terminar antes de qualquer SELECT.
        pg_conn.rollback()
        with pg_conn.cursor() as cur:
            cur.execute(_SQL_SOLTA, (chave_trava(sport, env),))
        pg_conn.commit()
    except Exception:
        pass
