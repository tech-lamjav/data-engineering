"""Configuração de logging."""
import json
import logging
import os
import sys
from src.config import LOG_LEVEL

# K_SERVICE é injetado automaticamente em todo Cloud Run revision
_CLOUD_RUN = os.getenv("K_SERVICE")


class _CloudRunFormatter(logging.Formatter):
    """Formatter JSON para Cloud Logging reconhecer severity corretamente."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "severity": record.levelname,
            "message": super().format(record),
            "logger": record.name,
        })


def setup_logger(name: str = __name__) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    if _CLOUD_RUN:
        handler.setFormatter(_CloudRunFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        ))

    logger.addHandler(handler)
    return logger

