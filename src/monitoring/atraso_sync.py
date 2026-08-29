"""Detector de atraso do sync BigQuery -> Postgres de serving.

Pergunta, de hora em hora: o Postgres que o app lê está com o mesmo dado que o BigQuery
já produziu? Compara, por tabela, o `modified` atual do BQ com o
`last_synced_bq_modified_time` gravado em `<schema>._sync_state` — que é exatamente a
comparação que o skip-if-unchanged do sync faz.

POR QUE ESTE DETECTOR VIVE NO GITHUB ACTIONS, e não num Cloud Run + Scheduler:
ele existe para enxergar o sync parado, e o sync é Cloud Run disparado por Workflow. Um
vigia hospedado na infra que ele vigia morre junto com ela — mesma razão pela qual o
detector de deriva de imagem do `analytics-engineering` vive fora da imagem dbt. Ver
docs/adr/0002.

POR QUE ATRASO E NÃO IDADE:
"a tabela foi sincronizada há mais de N horas" exige um N por tabela, porque as cadências
vão de 15 minutos (odds) a uma semana (`fact_team_season_stats`) — e uma tabela semanal
ficaria vermelha 6 dias por semana. O atraso normaliza isso sozinho: se o BQ não mudou,
não há o que sincronizar e o atraso é zero, qualquer que seja a cadência. Um limiar
global, não 22.

POR QUE 3 HORAS:
o sync roda de hora em hora e uma passada normal leva ~2 min, então 3h são 3 ciclos
perdidos. Não é mais apertado porque a recuperação de um atraso longo estoura os 900s de
timeout do Cloud Run e precisa de 2–3 passadas para se resolver sozinha (visto em
29/08/2026) — um limiar de 1h acenderia no meio de uma recuperação legítima.

POR QUE O E-MAIL SAI DAQUI, e não da notificação nativa do Actions:
notificação de workflow agendado vai APENAS para quem criou o workflow, e vermelho
persistente gera uma notificação por execução — ~24/dia. O incidente de 26–29/08/2026
durou 3 dias justamente porque o alarme que existia chegava todo dia e virou papel de
parede. Por isso este manda e-mail próprio e SÓ EM TRANSIÇÃO, com um lembrete a cada 24h
enquanto o vermelho durar.
"""
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from html import escape

from src.config import get_sync_target
from src.reporting.formatting import MUTED, RED, cell as _cell
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Ver "POR QUE 3 HORAS" no cabeçalho antes de mexer.
LIMIAR_ATRASO = timedelta(hours=3)

# Enquanto o estado continuar vermelho, um lembrete por dia. Sem ele, um vermelho que
# ninguém tratou some do radar depois do primeiro aviso; com cadência menor, vira o
# dilúvio que este detector existe para não ser.
INTERVALO_LEMBRETE = timedelta(hours=24)

# Nome do detector na tabela de estado. Uma linha por detector, para que um segundo
# detector no futuro não precise de outra tabela.
DETECTOR = "atraso_sync_futebol"

AMBIENTES = ("prd", "dev")


@dataclass(frozen=True)
class Atraso:
    """Quanto tempo o BQ está à frente do Postgres, para uma tabela."""

    tabela: str
    bq_modified: datetime
    sincronizado: datetime | None  # None = tabela nunca sincronizada
    atraso: timedelta

    @property
    def vermelho(self) -> bool:
        return self.atraso >= LIMIAR_ATRASO


@dataclass(frozen=True)
class EstadoDetector:
    """O que ficou gravado da última execução, por ambiente."""

    estado: str  # 'verde' | 'vermelho'
    desde: datetime | None
    ultimo_aviso_em: datetime | None


def calcula_atraso(
    tabela: str,
    bq_modified: datetime,
    sincronizado: datetime | None,
    agora: datetime,
) -> Atraso:
    """Atraso = há quanto tempo o BQ está à frente.

    Se o Postgres já tem o carimbo atual do BQ (`sincronizado >= bq_modified`), não há
    nada pendente: atraso zero, independentemente de quando o sync rodou pela última vez.
    É isso que dispensa um limiar por tabela.
    """
    if sincronizado is not None and sincronizado >= bq_modified:
        return Atraso(tabela, bq_modified, sincronizado, timedelta(0))
    # A conta é contra o carimbo do BQ, não contra o do Postgres: mede há quanto tempo o
    # dado novo existe sem ter chegado ao serving, que é o dano real para quem lê.
    return Atraso(tabela, bq_modified, sincronizado, agora - bq_modified)


def mede_ambiente(bq_client, pg_conn, dataset: str, schema: str, tabelas, agora) -> list[Atraso]:
    """Mede o atraso das tabelas da allowlist num ambiente."""
    with pg_conn.cursor() as cur:
        cur.execute(
            f'SELECT table_name, last_synced_bq_modified_time FROM "{schema}"."_sync_state"'
        )
        sincronizados = dict(cur.fetchall())

    atrasos = []
    for tabela in tabelas:
        # get_table() lê metadado: não escaneia bytes e não custa nada.
        bq_modified = bq_client.get_table(f"{dataset}.{tabela}").modified
        atrasos.append(calcula_atraso(tabela, bq_modified, sincronizados.get(tabela), agora))
    return atrasos


def le_estado(pg_conn, schema: str) -> EstadoDetector:
    """Lê o estado gravado. Ausência de linha conta como verde (primeira execução)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            f'SELECT estado, desde, ultimo_aviso_em FROM "{schema}"."_detector_state" '
            "WHERE detector = %s",
            (DETECTOR,),
        )
        row = cur.fetchone()
    if not row:
        return EstadoDetector("verde", None, None)
    return EstadoDetector(row[0], row[1], row[2])


def grava_estado(pg_conn, schema: str, estado: EstadoDetector) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{schema}"."_detector_state" '
            "(detector, estado, desde, ultimo_aviso_em) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (detector) DO UPDATE SET estado = EXCLUDED.estado, "
            "desde = EXCLUDED.desde, ultimo_aviso_em = EXCLUDED.ultimo_aviso_em",
            (DETECTOR, estado.estado, estado.desde, estado.ultimo_aviso_em),
        )
    pg_conn.commit()


def decide_envio(anterior: EstadoDetector, ha_vermelho: bool, agora: datetime):
    """Decide se este ciclo manda e-mail, e devolve (motivo, estado a gravar).

    motivo: 'abriu' | 'recuperou' | 'lembrete' | None (não manda).
    """
    era_vermelho = anterior.estado == "vermelho"

    if ha_vermelho and not era_vermelho:
        return "abriu", EstadoDetector("vermelho", agora, agora)

    if not ha_vermelho and era_vermelho:
        return "recuperou", EstadoDetector("verde", agora, agora)

    if ha_vermelho and era_vermelho:
        # `desde` preserva o início do episódio: é o número que o lembrete comunica.
        desde = anterior.desde or agora
        vencido = (
            anterior.ultimo_aviso_em is None
            or agora - anterior.ultimo_aviso_em >= INTERVALO_LEMBRETE
        )
        if vencido:
            return "lembrete", EstadoDetector("vermelho", desde, agora)
        # Silêncio deliberado: o episódio segue aberto e já foi avisado.
        return None, EstadoDetector("vermelho", desde, anterior.ultimo_aviso_em)

    return None, EstadoDetector("verde", anterior.desde, anterior.ultimo_aviso_em)


def _fmt_dur(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total}s"
    horas, resto = divmod(total, 3600)
    minutos = resto // 60
    return f"{horas}h{minutos:02d}m" if horas else f"{minutos}min"


def monta_email(por_ambiente: dict, motivos: dict, agora: datetime) -> tuple[str, str]:
    """Assunto + HTML. O assunto carrega ambiente e duração — quem lê decide sem abrir."""
    vermelhos = {
        env: [a for a in atrasos if a.vermelho] for env, atrasos in por_ambiente.items()
    }
    vermelhos = {env: v for env, v in vermelhos.items() if v}

    if vermelhos:
        pior = max((a.atraso for v in vermelhos.values() for a in v), default=timedelta(0))
        envs = ", ".join(sorted(vermelhos))
        token = "[ATRASO]" if "lembrete" not in motivos.values() else "[ATRASO · LEMBRETE]"
        subject = f"{token} Sync futebol atrasado — {envs} há {_fmt_dur(pior)}"
    else:
        subject = "[RECUPERADO] Sync futebol voltou a acompanhar o BigQuery"

    linhas = []
    for env in sorted(por_ambiente):
        for a in sorted(por_ambiente[env], key=lambda x: x.atraso, reverse=True):
            if not a.vermelho:
                continue
            visto = "nunca sincronizada" if a.sincronizado is None else "atrás do BigQuery"
            linhas.append(
                "<tr>"
                + _cell(escape(env))
                + _cell(escape(a.tabela))
                + _cell(_fmt_dur(a.atraso), align="right", color=RED)
                + _cell(escape(visto), color=MUTED)
                + "</tr>"
            )

    if linhas:
        corpo = (
            '<p>O BigQuery produziu dado que o Postgres de serving não recebeu. O app '
            "continua respondendo — com o dado da última sincronização bem-sucedida.</p>"
            '<table style="border-collapse:collapse;font-family:system-ui,sans-serif;'
            'font-size:13px">'
            '<tr style="background:#f6f8fa">'
            + _cell("<strong>ambiente</strong>")
            + _cell("<strong>tabela</strong>")
            + _cell("<strong>atraso</strong>", align="right")
            + _cell("<strong>situação</strong>")
            + "</tr>"
            + "".join(linhas)
            + "</table>"
            "<p style=\"color:#57606a;font-size:12px\">Limiar: "
            f"{_fmt_dur(LIMIAR_ATRASO)}. Onde olhar: execuções do "
            "<code>workflow-futebol-sync</code> e logs do serviço "
            "<code>sync-bq-to-postgres</code> (o abort por deriva de schema aparece como "
            "ERROR sem truncar nada).</p>"
        )
    else:
        corpo = (
            "<p>Todas as tabelas da allowlist voltaram a acompanhar o BigQuery. "
            "Nenhuma ação necessária.</p>"
        )

    html = (
        '<div style="font-family:system-ui,sans-serif;font-size:14px">'
        f"<h2 style=\"margin:0 0 12px\">Detector de atraso do sync</h2>{corpo}"
        f'<p style="color:#57606a;font-size:12px">Medido em '
        f'{escape(agora.isoformat(timespec="seconds"))} (UTC).</p></div>'
    )
    return subject, html


def envia_email(subject: str, html: str) -> None:
    """Mesmos secrets do resumo diário (GMAIL_USER / GMAIL_APP_PASSWORD / NOTIFY_EMAIL)."""
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


def _monta_resultado(por_ambiente: dict, motivos: dict) -> dict:
    """O que o script devolve: só o que é vermelho, para caber num log."""
    return {
        "vermelho": any(a.vermelho for v in por_ambiente.values() for a in v),
        "motivos": motivos,
        "por_ambiente": {
            env: [
                {"tabela": a.tabela, "atraso_s": a.atraso.total_seconds()}
                for a in atrasos
                if a.vermelho
            ]
            for env, atrasos in por_ambiente.items()
        },
    }


def roda_detector(agora: datetime | None = None) -> dict:
    """Mede os dois ambientes, decide o envio por ambiente e manda no máximo 1 e-mail.

    Não conserta nada de propósito: o agendamento horário do sync já é o retry, e
    re-disparar um sync que acabou de falhar empilha execução concorrente — que foi o que
    produziu o PARTIAL_FAILURE de 29/08/2026.
    """
    # Importados aqui para que os testes das funções puras não precisem de psycopg nem
    # das libs do Google, que só existem no requirements do runtime.
    import psycopg
    from google.cloud import bigquery

    from src.config import BIGQUERY_PROJECT_ID, get_pg_url_ro

    agora = agora or datetime.now(timezone.utc)
    dataset, schema, tabelas = get_sync_target("futebol")
    bq_client = bigquery.Client(project=BIGQUERY_PROJECT_ID)

    por_ambiente: dict[str, list[Atraso]] = {}
    motivos: dict[str, str] = {}
    pendentes = []

    # O finally cobre a MEDIÇÃO e o envio juntos: se o segundo ambiente levantar, as
    # conexões do primeiro já estão em `pendentes` e precisam fechar do mesmo jeito.
    try:
        for env in AMBIENTES:
            conn = psycopg.connect(get_pg_url_ro(env), connect_timeout=15)
            conn.autocommit = False
            try:
                atrasos = mede_ambiente(bq_client, conn, dataset, schema, tabelas, agora)
                por_ambiente[env] = atrasos
                ha_vermelho = any(a.vermelho for a in atrasos)
                anterior = le_estado(conn, schema)
                motivo, novo = decide_envio(anterior, ha_vermelho, agora)
                pendentes.append((conn, schema, novo))
                if motivo:
                    motivos[env] = motivo
                logger.info(
                    f"{env}: {sum(1 for a in atrasos if a.vermelho)}/{len(atrasos)} tabela(s) "
                    f"acima do limiar; estado {anterior.estado} -> {novo.estado}; "
                    f"motivo={motivo}"
                )
            except Exception:
                # Esta conexão ainda não entrou em `pendentes`; as anteriores fecham no
                # finally de fora.
                conn.close()
                raise

        resultado = _monta_resultado(por_ambiente, motivos)

        if motivos:
            subject, html = monta_email(por_ambiente, motivos, agora)
            if os.getenv("DETECTOR_DRY_RUN"):
                logger.info(f"[DRY_RUN] e-mail NAO enviado. subject={subject!r}")
            else:
                envia_email(subject, html)
                logger.info(f"E-mail enviado: {subject}")
            resultado["subject"] = subject
        else:
            logger.info("Sem transição de estado: nenhum e-mail neste ciclo.")

        # O estado só é gravado depois do envio: se o SMTP falhar, o próximo ciclo ainda vê
        # a transição e tenta de novo, em vez de marcar 'avisado' sem ter avisado.
        if not os.getenv("DETECTOR_DRY_RUN"):
            for conn, schema_, novo in pendentes:
                grava_estado(conn, schema_, novo)
    finally:
        for conn, _, _ in pendentes:
            conn.close()

    return resultado
