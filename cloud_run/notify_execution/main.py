import functions_framework
import os
import smtplib
from email.mime.text import MIMEText


GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
NOTIFY_EMAIL = os.environ["NOTIFY_EMAIL"]


@functions_framework.http
def notify_execution(request):
    """Recebe resumo de execução de workflow e envia email via Gmail SMTP."""
    body = request.get_json(silent=True) or {}
    workflow = body.get("workflow_name", "unknown")
    status = body.get("status", "UNKNOWN")
    duration = body.get("duration_seconds", 0)
    failed = body.get("failed_services", [])

    icon = "OK" if status == "SUCCESS" else "FALHA"
    subject = f"[{icon}] {workflow} — {status}"
    failed_text = "\n".join(f"  - {s}" for s in failed) if failed else "  Nenhum"
    content = (
        f"Workflow: {workflow}\n"
        f"Status: {status}\n"
        f"Duração: {int(duration)}s\n\n"
        f"Serviços com falha:\n{failed_text}"
    )

    msg = MIMEText(content)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = NOTIFY_EMAIL

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            smtp.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
    except Exception as e:
        # Não derrubar o handler silenciosamente; loga server-side e retorna 500.
        print(f"Erro ao enviar email de notificacao: {e}")
        return {"status": "error"}, 500

    return {"status": "notified"}, 200
