"""Tamanho do Postgres de DEV, medido ao fim do passe DEV do sync (DE#106).

O DEV é o projeto free do Supabase, com teto de 500 MB, e a política é desligar a escrita
quando ele passa. Até aqui nada avisava a aproximação: o estouro só aparecia quando alguém abria
o painel. O sync mede o tamanho com a conexão que já tem aberta e o devolve no resumo; o
workflow o põe no log de conclusão e o resumo diário alerta acima de 450 MB.

A métrica é a SOMA de `pg_database_size` sobre TODOS os bancos do cluster, que é a que o
Supabase usa para o teto (o banco `postgres` sozinho subconta; medido em 28/09: 654 MB num e
669 MB na soma). A unidade é MiB (1024²), a de `pg_size_pretty`.

Medir não pode derrubar o sync: ele já carregou as tabelas. Qualquer falha (permissão, timeout)
devolve None, o campo sai nulo e o resumo diário mostra "não medido", que é diferente de
"está bem". Depois de uma exceção a transação da sessão fica abortada; por isso o rollback.
"""
from typing import Optional

from src.utils.logger import setup_logger

logger = setup_logger(__name__)

_MIB = 1024 * 1024


def medir_tamanho_dev_mb(pg_conn) -> Optional[float]:
    """Soma de `pg_database_size` de todos os bancos do cluster, em MiB (1 casa), ou None.

    Recebe a conexão aberta do sync. Nunca levanta: soma nula ou erro devolvem None.
    """
    try:
        with pg_conn.cursor() as cur:
            cur.execute("SELECT sum(pg_database_size(datname)) FROM pg_database")
            linha = cur.fetchone()
        pg_conn.commit()
    except Exception as e:
        logger.warning(f"Tamanho do DEV não medido ({type(e).__name__}: {e}); campo sai nulo")
        try:
            pg_conn.rollback()
        except Exception:  # a conexão pode já estar morta; medir é só um extra
            pass
        return None
    if not linha or linha[0] is None:
        return None
    return round(float(linha[0]) / _MIB, 1)
