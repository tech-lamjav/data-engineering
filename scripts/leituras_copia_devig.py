"""Linha de base de leituras da cópia congelada de `int_futebol_odds_devig` (DE#112, hist. 43).

Sem argparse (regra .cursorrules). SOMENTE LEITURA: um SELECT em `pg_stat_user_tables`,
numa sessão marcada read-only. Roda por mão humana, nunca por workflow.

QUANDO RODAR
  1. No dia do congelamento — o dia em que o `sync-bq-to-postgres` sem o devig for
     redeployado (a cópia para de ser escrita naquela execução):
         .venv/bin/python3 scripts/leituras_copia_devig.py > leitura_devig_dia0.jsonl
     Cole as linhas no ticket do `DROP`.
  2. 7 dias depois, comparando com a anterior:
         LEITURA_ANTERIOR=leitura_devig_dia0.jsonl .venv/bin/python3 scripts/leituras_copia_devig.py
     Saída 0 = estável nos ambientes medidos (o `DROP` pode ser pedido ao Victor);
     1 = alguém leu, cedo demais ou medição inválida (ver o veredito impresso).

VARIÁVEIS
  LEITURA_ENVS      ambientes separados por vírgula (default "prd,dev").
  LEITURA_ANTERIOR  caminho do arquivo da primeira leitura; liga o modo de comparação.

Usa o papel de leitura (`SUPABASE_PG_URL_<ENV>_RO`); sem ele cai na URL de escrita, mas a
sessão continua read-only. Ver src/monitoring/leituras_copia.py para o porquê de cada regra.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import FUTEBOL_MART_PG_SCHEMA, get_pg_url_ro
from src.monitoring.leituras_copia import (
    compara,
    indexa_por_env,
    le_leitura,
    para_json,
)
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

TABELA = "int_futebol_odds_devig"


def _le_ambiente(env: str):
    import psycopg

    with psycopg.connect(get_pg_url_ro(env), connect_timeout=15) as conn:
        # Antes de qualquer statement: o servidor passa a recusar escrita nesta sessão.
        conn.read_only = True
        return le_leitura(conn, env, FUTEBOL_MART_PG_SCHEMA, TABELA)


def main():
    try:
        envs = [e.strip() for e in os.getenv("LEITURA_ENVS", "prd,dev").split(",") if e.strip()]
        caminho_anterior = os.getenv("LEITURA_ANTERIOR")
        anteriores = (
            indexa_por_env(Path(caminho_anterior).read_text(encoding="utf-8"))
            if caminho_anterior
            else {}
        )

        todos_estaveis = True
        for env in envs:
            leitura = _le_ambiente(env)
            print(para_json(leitura))
            if not caminho_anterior:
                continue
            base = anteriores.get(env)
            if base is None:
                logger.error(f"{env}: sem registro anterior em {caminho_anterior}")
                todos_estaveis = False
                continue
            r = compara(base, leitura)
            logger.info(
                f"{env}: veredito={r.veredito} intervalo={r.intervalo} "
                f"delta_seq_scan={r.delta_seq_scan} delta_idx_scan={r.delta_idx_scan} "
                f"delta_n_live_tup={r.delta_n_live_tup}"
            )
            todos_estaveis = todos_estaveis and r.veredito == "estavel"
        return 0 if todos_estaveis else 1
    except Exception as e:
        logger.error(f"Erro: {str(e)}", exc_info=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
