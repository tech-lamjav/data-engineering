"""Grava (ou confere) a saída da RPC get_futebol_fixture_quotes de uma amostra de fixtures (DE#109).

SOMENTE LEITURA no PRD. Sem argparse (regra .cursorrules): tudo por variável de ambiente.

    # ANTES de ligar o cache de serving: grava a linha de base (nunca sobrescreve o arquivo)
    CAPTURA_RPC_MODO=captura CAPTURA_RPC_ARQUIVO=~/rpc_quotes_pre_cache.json \\
        .venv/bin/python3 scripts/captura_rpc_quotes.py

    # DEPOIS da primeira carga de PRD com o cache: reconsulta os MESMOS ids e compara
    CAPTURA_RPC_MODO=diff CAPTURA_RPC_ARQUIVO=~/rpc_quotes_pre_cache.json \\
        .venv/bin/python3 scripts/captura_rpc_quotes.py

CAPTURA_RPC_N = fixtures por grupo (recente, futura, antiga); padrão 15.
O que é falha e o que é aviso está em src/monitoring/captura_rpc_quotes.py.

CÓDIGO DE SAÍDA  0 = linha de base gravada ou diff verde; 1 = diff vermelho; 2 = erro.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.monitoring.captura_rpc_quotes import executa
from src.utils.logger import setup_logger

logger = setup_logger(__name__)
for _h in logger.handlers:
    _h.setStream(sys.stderr)

N_POR_GRUPO_PADRAO = 15


def abre_conexao_prd():
    import psycopg

    from src.config import get_pg_url

    return psycopg.connect(get_pg_url("prd"), connect_timeout=15)


def main(ambiente=None, conexao=None):
    ambiente = os.environ if ambiente is None else ambiente
    try:
        modo = ambiente.get("CAPTURA_RPC_MODO", "")
        arquivo = ambiente.get("CAPTURA_RPC_ARQUIVO", "")
        if not arquivo:
            raise ValueError("defina CAPTURA_RPC_ARQUIVO (onde a linha de base fica gravada)")
        n = int(ambiente.get("CAPTURA_RPC_N", N_POR_GRUPO_PADRAO))
        agora = datetime.now(timezone.utc)
        arquivo = os.path.expanduser(arquivo)
        if conexao is not None:
            codigo, texto = executa(modo, arquivo, conexao, n, agora)
        else:
            with abre_conexao_prd() as conn:
                codigo, texto = executa(modo, arquivo, conn, n, agora)
    except Exception as e:
        logger.error(f"Erro: {e}", exc_info=True)
        return 2
    print(texto)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
