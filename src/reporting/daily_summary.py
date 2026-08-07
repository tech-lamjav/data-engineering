"""Resumo diário de execuções de workflows.

Lê o Cloud Logging do dia anterior (America/Sao_Paulo), agrega o status final de
cada execução de TODOS os workflows e envia 1 email HTML consolidado via Gmail SMTP
(mesmos secrets do notify-execution). Substitui o modelo antigo de 1 email por
execução — que não escala com os polls de 15min.

Fonte primária: o passo `log_completion` que todo workflow emite no fim (jsonPayload
com `status` presente). Schema observado nos workflows:
  - full workflows: {workflow_name, status, duration_seconds, failed_services[], failed_count, (futebol) mode}
  - polls 15min:    {workflow_name, status, duration_seconds, saved_count}

Camada de robustez: cruza com a Workflow Executions API p/ capturar execuções que
falharam de forma "dura" (state FAILED/CANCELLED) sem chegar a emitir o log_completion.

Para testar localmente sem mandar email: SUMMARY_DRY_RUN=1 (pula o send_email).
"""
import os
import smtplib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from html import escape

from google.cloud import logging as gcloud_logging
from google.cloud.workflows.executions_v1 import ExecutionsClient
from google.cloud.workflows.executions_v1.types import (
    Execution,
    ExecutionView,
    ListExecutionsRequest,
)

from src.config import GCP_PROJECT_ID
from src.reporting.api_quota import build_quota_section, collect_quota
from src.reporting.guardas import build_guardas_section
from src.reporting.formatting import SAO_PAULO, cell as _cell, fmt_brt as _fmt_brt
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

LOCATION = "us-east1"

# workflow_id (com hífen) de todos os workflows — usado pela Executions API e como
# chave canônica de agregação. jsonPayload.workflow_name vem com underscore; tudo é
# normalizado p/ hífen (_normalize) antes de agregar, senão o mesmo workflow vira 2 linhas.
ALL_WORKFLOWS = [
    "workflow-data-engineering",
    "workflow-injury-report",
    "workflow-bets",
    "workflow-futebol",
    "workflow-futebol-lineups",
    "workflow-futebol-team-stats",
    "workflow-futebol-odds",
    "workflow-futebol-predictions",
    "workflow-futebol-injuries",
    "workflow-futebol-sync",
    "workflow-daily-summary",
]


def _normalize(name: str) -> str:
    """workflow_futebol_odds -> workflow-futebol-odds (chave canônica)."""
    return (name or "unknown").replace("_", "-")


def _as_utc(dt: datetime | None) -> datetime | None:
    """Garante datetime tz-aware em UTC (defensivo p/ timestamps naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class WFAgg:
    """Agregado por workflow na janela do dia."""

    total: int = 0
    success: int = 0
    partial: int = 0
    failed: int = 0  # FAILED/CANCELLED no nível plataforma (sem log_completion)
    saved_count: int = 0
    durations: list = field(default_factory=list)
    last_run: datetime | None = None  # UTC
    failures: list = field(default_factory=list)  # (dt_utc, status, detalhe)
    # Guardas de dado (tag:guarda) vermelhas: timestamps (UTC) das execucoes em que
    # `guardas_status` veio != SUCCESS. Separado de `failures` porque guarda vermelha NAO
    # e falha do workflow — nao pode derrubar o board, so precisa alarmar.
    guardas_red: list = field(default_factory=list)


def compute_window(target_date: date | None = None):
    """Janela do dia calendário (default: anterior) em America/Sao_Paulo, em UTC.

    Retorna (dia, start_utc, end_utc), com end exclusivo. BRT é UTC-3 fixo (sem
    horário de verão), mas usamos ZoneInfo + astimezone p/ ficar correto mesmo que
    o tz database mude.
    """
    if target_date is None:
        target_date = datetime.now(SAO_PAULO).date() - timedelta(days=1)
    start_sp = datetime(target_date.year, target_date.month, target_date.day, tzinfo=SAO_PAULO)
    end_sp = start_sp + timedelta(days=1)
    return target_date, start_sp.astimezone(timezone.utc), end_sp.astimezone(timezone.utc)


def _logging_filter(start_utc: datetime, end_utc: datetime) -> str:
    return (
        'resource.type="workflows.googleapis.com/Workflow" '
        f'AND timestamp>="{start_utc.isoformat()}" '
        f'AND timestamp<"{end_utc.isoformat()}" '
        'AND jsonPayload.status:*'
    )


def collect_from_logging(client, start_utc, end_utc, agg) -> int:
    """Agrega o log_completion (status final) de cada execução na janela.

    O iterador de list_entries pagina sozinho (next_page_token).
    """
    flt = _logging_filter(start_utc, end_utc)
    count = 0
    for entry in client.list_entries(
        resource_names=[f"projects/{GCP_PROJECT_ID}"],
        filter_=flt,
        page_size=1000,
    ):
        payload = entry.payload
        if not isinstance(payload, dict):
            continue
        status = payload.get("status")
        if not status:
            continue
        count += 1
        a = agg[_normalize(payload.get("workflow_name", "unknown"))]
        a.total += 1
        ts = _as_utc(entry.timestamp)
        if status == "SUCCESS":
            a.success += 1
        else:
            a.partial += 1
            failed_services = payload.get("failed_services") or []
            detail = ", ".join(str(s) for s in failed_services) if failed_services else "—"
            a.failures.append((ts, status, detail))
        # Status PROPRIO das guardas, emitido pelos workflows futebol/odds. Ausente nos
        # demais workflows (que nao rodam guardas) — ausencia nao e vermelho.
        guardas = payload.get("guardas_status")
        if guardas and guardas != "SUCCESS":
            a.guardas_red.append(ts)
        try:
            a.saved_count += int(payload.get("saved_count") or 0)
        except (TypeError, ValueError):
            pass
        dur = payload.get("duration_seconds")
        if dur is not None:
            try:
                a.durations.append(float(dur))
            except (TypeError, ValueError):
                pass
        if ts is not None and (a.last_run is None or ts > a.last_run):
            a.last_run = ts
    logger.info(f"Logging: {count} execucoes agregadas na janela")
    return count


def collect_crashed_executions(client, start_utc, end_utc, agg) -> int:
    """Robustez: conta execucoes FAILED/CANCELLED (que nunca emitem log_completion).

    Sem dupla contagem: PARTIAL_FAILURE termina como SUCCEEDED no nivel plataforma
    (o corpo do workflow trata o proprio erro) e ja foi contado 1x pelo Logging;
    aqui so entram FAILED/CANCELLED, que nao produzem log_completion.
    """
    total_crashed = 0
    for wf in ALL_WORKFLOWS:
        parent = f"projects/{GCP_PROJECT_ID}/locations/{LOCATION}/workflows/{wf}"
        req = ListExecutionsRequest(parent=parent, view=ExecutionView.BASIC, page_size=100)
        try:
            for ex in client.list_executions(request=req):
                st = _as_utc(ex.start_time)
                if st is None:
                    continue
                if st < start_utc:
                    # resultados vem do mais recente p/ o mais antigo -> pode parar
                    break
                if st >= end_utc:
                    continue
                if ex.state in (Execution.State.FAILED, Execution.State.CANCELLED):
                    a = agg[_normalize(wf)]
                    a.total += 1
                    a.failed += 1
                    total_crashed += 1
                    err = ""
                    if ex.error is not None:
                        err = ex.error.context or ex.error.payload or ""
                    a.failures.append((st, ex.state.name, (err or ex.state.name)[:300]))
                    if a.last_run is None or st > a.last_run:
                        a.last_run = st
        except Exception as e:
            logger.warning(f"list_executions falhou para {wf}: {e}")
    logger.info(f"Executions API: {total_crashed} execucoes FAILED/CANCELLED extras")
    return total_crashed


def _fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def build_html(day: date, agg: dict, quota=None) -> tuple[str, str]:
    """Monta (subject, html) do email consolidado. Sempre renderiza (mesmo vazio).

    `quota` (QuotaInfo | None) acrescenta a seção de cota da API-Football; None omite a
    seção, mantendo o email de antes intacto.
    """
    total_runs = sum(a.total for a in agg.values())
    total_ok = sum(a.success for a in agg.values())
    total_partial = sum(a.partial for a in agg.values())
    total_failed = sum(a.failed for a in agg.values())
    has_problems = (total_partial + total_failed) > 0

    # Guarda vermelha nao entra em has_problems de proposito: ela nao e falha de workflow.
    # Mas TEM que aparecer no assunto — [OK] com guarda vermelha e alarme mudo, que era
    # exatamente o buraco do C4.
    vermelhas = {wf: a.guardas_red for wf, a in agg.items() if a.guardas_red}
    total_guardas_red = sum(len(v) for v in vermelhas.values())

    # Tokens JUSTAPOSTOS ("[FALHAS][GUARDA]") e não fundidos ("[FALHAS+GUARDA]"): filtro de
    # caixa de entrada casa por substring, e o fundido não casaria nem com "[FALHAS]" nem com
    # "[GUARDA]" — falharia exatamente no pior dia, o que tem os dois. Justapor mantém uma
    # regra pré-existente em "[FALHAS]" funcionando e deixa "[GUARDA]" casar sozinho.
    flags = []
    if has_problems:
        flags.append("[FALHAS]")
    if vermelhas:
        flags.append("[GUARDA]")
    subject = f"{''.join(flags) or '[OK]'} Resumo diario de workflows — {day.isoformat()}"

    resumo_guardas = (
        f' · <strong style="color:#cf222e">{total_guardas_red} com guarda vermelha</strong>'
        if total_guardas_red
        else ""
    )
    head = (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#1f2328;max-width:860px">'
        f'<h2 style="margin:0 0 4px">Resumo diario de workflows — {day.isoformat()}</h2>'
        '<p style="margin:0 0 14px;color:#57606a;font-size:13px">'
        f'{total_runs} execucoes · {total_ok} OK · {total_partial} parcial · '
        f'{total_failed} falha{resumo_guardas}. Horarios em America/Sao_Paulo (BRT).</p>'
    )

    rows = []
    for wf in sorted(agg.keys()):
        a = agg[wf]
        if a.total == 0:
            continue
        bg = "#fff5f5" if (a.partial + a.failed) else "#ffffff"
        avg = _fmt_dur(sum(a.durations) / len(a.durations)) if a.durations else "—"
        mx = _fmt_dur(max(a.durations)) if a.durations else "—"
        rows.append(
            f'<tr style="background:{bg}">'
            + _cell(escape(wf))
            + _cell(a.total, "right")
            + _cell(a.success, "right", "#1a7f37")
            + _cell(a.partial or "", "right", "#9a6700")
            + _cell(a.failed or "", "right", "#cf222e")
            + _cell(a.saved_count or "", "right")
            + _cell(avg, "right")
            + _cell(mx, "right")
            + _cell(_fmt_brt(a.last_run), "right")
            + "</tr>"
        )

    if rows:
        header_cells = "".join(
            f'<th style="padding:6px 10px;border:1px solid #ddd;background:#f6f8fa;text-align:{al}">{label}</th>'
            for label, al in [
                ("Workflow", "left"), ("Runs", "right"), ("OK", "right"),
                ("Parcial", "right"), ("Falha", "right"), ("Itens", "right"),
                ("Dur med", "right"), ("Dur max", "right"), ("Ultimo", "right"),
            ]
        )
        table = (
            '<table style="border-collapse:collapse;font-size:13px;width:100%">'
            f"<thead><tr>{header_cells}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        table = '<p style="color:#57606a">Nenhuma execucao no periodo.</p>'

    # Detalhe de falhas/parciais
    fail_rows = []
    for wf in sorted(agg.keys()):
        for ts, status, detail in sorted(agg[wf].failures, key=lambda x: (x[0] or datetime.min.replace(tzinfo=timezone.utc))):
            color = "#cf222e" if status in ("FAILED", "CANCELLED") else "#9a6700"
            fail_rows.append(
                "<tr>"
                + _cell(escape(wf))
                + _cell(_fmt_brt(ts), "right")
                + _cell(escape(status), "left", color)
                + _cell(escape(detail or "—"))
                + "</tr>"
            )
    fail_section = ""
    if fail_rows:
        fail_section = (
            '<h3 style="margin:18px 0 6px">Detalhe de falhas / parciais</h3>'
            '<table style="border-collapse:collapse;font-size:13px;width:100%">'
            '<thead><tr>'
            '<th style="padding:6px 10px;border:1px solid #ddd;background:#f6f8fa;text-align:left">Workflow</th>'
            '<th style="padding:6px 10px;border:1px solid #ddd;background:#f6f8fa;text-align:right">Hora</th>'
            '<th style="padding:6px 10px;border:1px solid #ddd;background:#f6f8fa;text-align:left">Status</th>'
            '<th style="padding:6px 10px;border:1px solid #ddd;background:#f6f8fa;text-align:left">Detalhe</th>'
            f"</tr></thead><tbody>{''.join(fail_rows)}</tbody></table>"
        )

    # Seções extras entram aqui, uma função por seção. Guardas antes da cota: guarda
    # vermelha é acionável hoje, cota é acompanhamento.
    guardas_section = build_guardas_section(vermelhas)
    quota_section = build_quota_section(quota, day)

    html = head + table + fail_section + guardas_section + quota_section + "</div>"
    return subject, html


def send_email(subject: str, html: str) -> None:
    """Envia o email HTML via Gmail SMTP (mesmos secrets do notify-execution)."""
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ["NOTIFY_EMAIL"]

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.sendmail(user, to, msg.as_string())


def run_daily_summary(target_date: date | None = None) -> dict:
    """Orquestra: janela -> Logging -> Executions API -> email. Sempre envia."""
    day, start_utc, end_utc = compute_window(target_date)
    logger.info(f"Resumo diario p/ {day.isoformat()} (UTC {start_utc.isoformat()} .. {end_utc.isoformat()})")

    agg: dict = defaultdict(WFAgg)
    try:
        collect_from_logging(gcloud_logging.Client(project=GCP_PROJECT_ID), start_utc, end_utc, agg)
    except Exception as e:
        # Fonte primária: não derrubar o resumo inteiro se o Logging falhar — segue com o
        # que a Executions API trouxer (operador não fica totalmente às cegas no dia).
        logger.error(f"Cloud Logging indisponivel, resumo seguira so com a Executions API: {e}", exc_info=True)
    try:
        collect_crashed_executions(ExecutionsClient(), start_utc, end_utc, agg)
    except Exception as e:
        logger.warning(f"Executions API indisponivel, seguindo so com Logging: {e}")

    # 1 chamada/dia ao /status. collect_quota nunca levanta: falha vira seção degradada.
    quota = collect_quota()

    subject, html = build_html(day, agg, quota)

    if os.getenv("SUMMARY_DRY_RUN"):
        logger.info(f"[DRY_RUN] email NAO enviado. subject={subject!r}")
    else:
        send_email(subject, html)
        logger.info(f"Email enviado: {subject}")

    return {
        "date": day.isoformat(),
        "emailed": not bool(os.getenv("SUMMARY_DRY_RUN")),
        # Chave nova (aditiva — o workflow não mapeia campos do body). Cai no Cloud
        # Logging junto da resposta do Cloud Run: série histórica de cota de graça.
        "quota": quota.as_log_dict(day),
        "totals": {
            "runs": sum(a.total for a in agg.values()),
            "success": sum(a.success for a in agg.values()),
            "partial": sum(a.partial for a in agg.values()),
            "failed": sum(a.failed for a in agg.values()),
        },
        "workflows": {
            wf: {
                "total": a.total,
                "success": a.success,
                "partial": a.partial,
                "failed": a.failed,
                "saved_count": a.saved_count,
            }
            for wf, a in sorted(agg.items())
            if a.total
        },
    }
