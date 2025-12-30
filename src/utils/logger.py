"""Configuração de logging."""
import logging
import sys
from src.config import LOG_LEVEL

def setup_logger(name: str = __name__) -> logging.Logger:
    """
    Configura e retorna um logger.
    
    Args:
        name: Nome do logger (geralmente __name__)
    
    Returns:
        Logger configurado
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    
    # Evita duplicação de handlers
    if logger.handlers:
        return logger
    
    # Handler para console
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    
    # Formato de log estruturado
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

