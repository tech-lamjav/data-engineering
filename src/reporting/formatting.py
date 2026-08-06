"""Primitivas de formatação compartilhadas pelas seções do e-mail do resumo diário.

Moram fora de `daily_summary.py` para que as seções (cota agora, status das guardas
depois) sejam montadas e testadas sem arrastar google-cloud-logging/workflows — que o
resumo importa e que vivem só no requirements do Cloud Run, não no de teste.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

# Todos os horários do e-mail são BRT. Não sobe p/ src/config.py de propósito: config é
# importado por TODO extractor, e as imagens dos serviços de extração não trazem `tzdata`
# — construir um ZoneInfo lá quebraria os deploys que hoje não dependem de timezone.
SAO_PAULO = ZoneInfo("America/Sao_Paulo")

# Paleta do e-mail (mesmos tons já usados na tabela de workflows).
GREEN = "#1a7f37"
AMBER = "#9a6700"
RED = "#cf222e"
MUTED = "#57606a"


def fmt_brt(dt_utc: datetime | None) -> str:
    """HH:MM em America/Sao_Paulo."""
    return dt_utc.astimezone(SAO_PAULO).strftime("%H:%M") if dt_utc else "—"


def cell(value, align="left", color=None) -> str:
    """<td> com o estilo inline usado em todas as tabelas do e-mail."""
    style = f"padding:6px 10px;border:1px solid #ddd;text-align:{align}"
    if color:
        style += f";color:{color}"
    return f'<td style="{style}">{value}</td>'
