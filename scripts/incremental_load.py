"""Script para ingestão incremental de dados (preparado para implementação futura)."""
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Adiciona o diretório raiz ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def incremental_load(season: int = SEASON, start_date: str = None, end_date: str = None):
    """
    Extrai dados incrementais baseado em datas.
    
    Esta função será implementada na próxima fase do projeto.
    A ideia é:
    1. Verificar última data processada (armazenada no GCS ou em banco de dados)
    2. Extrair apenas dados novos desde a última execução
    3. Atualizar marcador de última execução
    
    Args:
        season: Ano da temporada
        start_date: Data inicial (YYYY-MM-DD)
        end_date: Data final (YYYY-MM-DD)
    """
    logger.info("Ingestão incremental ainda não implementada.")
    logger.info("Esta funcionalidade será desenvolvida na próxima fase.")
    logger.info(f"Parâmetros recebidos: season={season}, start_date={start_date}, end_date={end_date}")
    
    # TODO: Implementar lógica de ingestão incremental
    # 1. Buscar última data processada
    # 2. Determinar range de datas a processar
    # 3. Extrair dados apenas para esse range
    # 4. Atualizar última data processada


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Ingestão incremental de dados da NBA")
    parser.add_argument(
        "--season",
        type=int,
        default=SEASON,
        help=f"Ano da temporada (default: {SEASON})",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        help="Data inicial (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="Data final (YYYY-MM-DD)",
    )
    
    args = parser.parse_args()
    
    try:
        incremental_load(
            season=args.season,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    except KeyboardInterrupt:
        logger.info("Processo interrompido pelo usuário")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Erro fatal: {str(e)}", exc_info=True)
        sys.exit(1)

