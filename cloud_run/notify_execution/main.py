import functions_framework
import os
import smtplib
from email.mime.text import MIMEText


@functions_framework.http
def notify_execution(request):
    """Recebe resumo de execução de workflow e envia email via Gmail SMTP."""
    # Leitura lazy dos segredos dentro do handler: evita crash de cold start
    # (KeyError no import) se um secret não estiver montado; devolve 500 claro.
    missing = [
        name
        for name in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL")
        if not os.environ.get(name)
    ]
    if missing:
        msg = f"config ausente: {', '.join(missing)}"
        print(f"Erro de configuracao no notify_execution: {msg}")
        return {"status": "error", "error": msg}, 500

    gmail_user = os.environ["GMAIL_USER"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    notify_email = os.environ["NOTIFY_EMAIL"]

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
    msg["From"] = gmail_user
    msg["To"] = notify_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(gmail_user, gmail_app_password)
            smtp.sendmail(gmail_user, notify_email, msg.as_string())
    except Exception as e:
        # Não derrubar o handler silenciosamente; loga server-side e retorna 500.
        print(f"Erro ao enviar email de notificacao: {e}")
        return {"status": "error"}, 500

    return {"status": "notified"}, 200
