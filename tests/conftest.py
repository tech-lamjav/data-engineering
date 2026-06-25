"""Configuração compartilhada dos testes.

Adiciona a raiz do repo ao sys.path (para `import src...`) e injeta env vars
dummy antes de qualquer import de `src.config` (que valida/lê env no import).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Valores dummy só para o import de src.config não falhar em ambiente de teste/CI.
for key, val in {
    "BALLDONTLIE_KEY": "test-key",
    "API_FOOTBALL_KEY": "test-key",
    "GCS_BUCKET_NAME": "test-bucket",
    "GCP_PROJECT_ID": "test-project",
    "SEASON": "2025",
    "LOG_LEVEL": "INFO",
}.items():
    os.environ.setdefault(key, val)
