"""Script que gera docs/contrato-serving-gerado.md a partir do pg_proc do PRD.

Sem argparse (regra .cursorrules).

    .venv/bin/python3 scripts/gera_contrato_serving.py           # escreve o arquivo
    CONTRATO_CHECK=1 .venv/bin/python3 scripts/gera_contrato_serving.py   # só compara

No modo check sai com 1 se o arquivo commitado divergir do banco — é assim que o CI
semanal acusa RPC criada, removida ou que passou a ler outra coluna sem ninguém registrar.

O modo check também confere a lista de MERCADOS SERVIDOS contra as RPCs vivas (DE#109, história
38): sai com 1 se uma RPC que lê `fact_odds_snapshot` citar um mercado fora de
`alvo.MERCADOS_SERVIDOS`, porque o cache de serving de PRD o cortou e a tela ficaria "sem
cotação" sem alarme. Só leitura.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.monitoring.contrato_serving import confere_mercados_servidos, gera
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

DESTINO = Path(__file__).parent.parent / "docs" / "contrato-serving-gerado.md"


def main():
    try:
        conteudo = gera()
    except Exception as e:
        logger.error(f"Erro: {str(e)}", exc_info=True)
        return 2

    if os.getenv("CONTRATO_CHECK"):
        atual = DESTINO.read_text(encoding="utf-8") if DESTINO.exists() else ""
        codigo = 0
        if atual == conteudo:
            logger.info("Mapa commitado bate com o PRD.")
        else:
            logger.error(
                f"{DESTINO.name} diverge do PRD. Rode "
                f"`.venv/bin/python3 scripts/gera_contrato_serving.py` e commite o resultado."
            )
            codigo = 1
        try:
            fora_da_lista = confere_mercados_servidos()
        except Exception as e:
            logger.error(f"Erro: {str(e)}", exc_info=True)
            return 2
        for mensagem in fora_da_lista:
            logger.error(mensagem)
        if fora_da_lista:
            logger.error(
                "Mercado fora da lista de servidos: o cache de serving o corta do PRD. Acrescente-o "
                "a `MERCADOS_SERVIDOS_NOMES` em src/sync/alvo.py (e some a carga), ou corrija a RPC."
            )
            codigo = 1
        else:
            logger.info("Nenhuma RPC cita mercado fora da lista de servidos.")
        return codigo

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(conteudo, encoding="utf-8")
    logger.info(f"Escrito: {DESTINO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
