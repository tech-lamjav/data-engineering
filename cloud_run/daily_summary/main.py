import functions_framework
import os
import sys
from datetime import date

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)
sys.path.insert(0, os.path.join(current_dir, "scripts"))

from src.reporting.daily_summary import run_daily_summary


@functions_framework.http
def daily_summary(request):
    """Gera e envia o resumo diario de execucoes de workflows (1 email/dia).

    Query params:
        date: YYYY-MM-DD (opcional). Default = dia anterior em America/Sao_Paulo.
              Util p/ teste/backfill de um dia especifico (dentro da retencao de logs).
    """
    date_arg = request.args.get("date")
    target = None
    if date_arg:
        try:
            target = date.fromisoformat(date_arg)
        except ValueError:
            # Erro do cliente (formato de data) → 400, não 500.
            return {"status": "error", "error": f"date invalido: {date_arg!r} (use YYYY-MM-DD)"}, 400
    try:
        result = run_daily_summary(target_date=target)
        return {"status": "success", **result}, 200
    except Exception as e:
        # Erro de execução: loga server-side, não ecoa str(e) cru ao chamador.
        print(f"Erro no daily_summary: {e}")
        return {"status": "error"}, 500
