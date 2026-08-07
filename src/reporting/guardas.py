"""Seção de guardas de qualidade de dado do e-mail do resumo diário (C4).

As guardas (`tag:guarda`) rodam numa fase dedicada dos workflows futebol/odds, DEPOIS do
mart — de propósito, para que teste vermelho não pule os modelos a jusante e congele o
board. Por isso guarda vermelha **não** vira `PARTIAL_FAILURE` do workflow: ela vai para
uma variável própria, `guardas_status`, que o `log_completion` emite no Cloud Logging.

O que faltava era o outro lado do fio: o resumo diário lia só `status` e descartava
`guardas_status`, então guarda vermelha com workflow verde saía como uma linha 100% verde.
Alarme que não alarma. Esta seção é o consumidor que faltava.

Fora de `daily_summary.py` pelo mesmo motivo que `api_quota.py`: dá para montar e testar a
seção sem arrastar google-cloud-logging/workflows, que só existem no requirements do Cloud Run.
"""
from datetime import datetime
from html import escape

from src.reporting.formatting import RED, MUTED, cell as _cell, fmt_brt as _fmt_brt


def build_guardas_section(vermelhas: dict[str, list[datetime]]) -> str:
    """Seção das guardas vermelhas do dia.

    `vermelhas` mapeia workflow -> timestamps (UTC) das execuções em que `guardas_status`
    veio diferente de SUCCESS. Retorna "" quando nenhuma acendeu — dia limpo não ganha
    seção, senão o alarme vira ruído de fundo e para de ser lido.
    """
    if not vermelhas:
        return ""

    linhas = []
    for wf in sorted(vermelhas):
        quando = [t for t in vermelhas[wf] if t is not None]
        ultima = max(quando) if quando else None
        linhas.append(
            '<tr style="background:#fff5f5">'
            + _cell(escape(wf))
            + _cell(len(vermelhas[wf]), "right", RED)
            + _cell(_fmt_brt(ultima), "right")
            + "</tr>"
        )

    cabecalho = "".join(
        f'<th style="padding:6px 10px;border:1px solid #ddd;background:#f6f8fa;text-align:{al}">{rotulo}</th>'
        for rotulo, al in [("Workflow", "left"), ("Execucoes", "right"), ("Ultima", "right")]
    )

    return (
        f'<h3 style="margin:18px 0 6px;color:{RED}">Guardas de qualidade de dado (tag:guarda)</h3>'
        f'<p style="margin:0 0 8px;color:{MUTED};font-size:13px">'
        "Guarda vermelha NAO derruba o workflow nem o board (por design) — este e-mail e o "
        "unico canal de alarme dela. O <code>guardas_status</code> nao diz QUAL teste falhou: "
        'ver a linha "Done. PASS=/ERROR=" no log do job <code>dbt-futebol</code> da execucao.</p>'
        '<table style="border-collapse:collapse;font-size:13px">'
        f"<thead><tr>{cabecalho}</tr></thead><tbody>{''.join(linhas)}</tbody></table>"
    )
