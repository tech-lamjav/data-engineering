"""Cota da API-Football no resumo diário: leitura do /status, limiares e seção HTML.

Por que fica fora de `daily_summary.py`: o resumo importa google-cloud-logging e
google-cloud-workflows, que só existem no requirements do Cloud Run. Aqui moram a
leitura, as duas decisões de alerta e a renderização — todas testáveis sem rede e sem
SDK de GCP (`tests/test_daily_summary_quota.py`). O `daily_summary` só faz a fiação.

⚠️ O que o número significa. `requests.current` do /status é o contador do dia CORRENTE
da API no instante da chamada — não o consumo de um dia fechado. O resumo roda às 00:05
BRT (03:05 UTC), então o valor lido cobre apenas as primeiras horas do dia da API — é um
PISO, não o consumo do dia. O alerta de vencimento não depende do horário e vale
integralmente.

⚠️ DE#80 — a leitura de fim de dia é a que o e-mail mostra. O reset real da cota é 00:00
UTC (doc oficial da API-Football), que em BRT (UTC-3, sem horário de verão) é 21:00. A
leitura de madrugada acima cai só 3h05min depois desse reset e é estruturalmente baixa —
por isso a seção NÃO a exibe quando há uma leitura de fim de dia disponível (pedido do
Victor: um número só, o do dia inteiro, sem o piso de madrugada do lado confundindo). A
leitura de madrugada só volta a aparecer — rotulada "parcial" — quando não houve leitura
de fim de dia elegível naquele dia (o poll de fixtures-live não rodou perto do reset).
`EodQuotaReading`/`select_eod_reading`/`collect_quota_eod` escolhem, entre as leituras de
`quota_remaining` que o poll de fixtures-live (a cada 15min) já loga de graça no
`log_completion`, a mais próxima do reset SEM passar dele — nunca a de depois, que
pertenceria ao próximo "dia da API". Sem chamada nova à API, sem infra nova. O alerta de
consumo (`QUOTA_ALERT_PCT`) agora é calculado sobre essa leitura de fim de dia quando ela
existe — o piso de madrugada quase nunca passava do limiar, então o alerta raramente
disparava; contra o total do dia ele passa a significar algo.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from src.clients.api_football_client import ApiFootballClient
from src.config import QUOTA_ALERT_PCT, SUBSCRIPTION_ALERT_DAYS
from src.reporting.formatting import AMBER, MUTED, RED, SAO_PAULO, cell, fmt_brt
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class QuotaInfo:
    """Leitura do /status: consumo do dia da API e vigência do plano.

    `error` preenchido = leitura degradada — a seção ainda renderiza, dizendo por quê.
    """

    current: Optional[int] = None
    limit_day: Optional[int] = None
    plan: Optional[str] = None
    subscription_end: Optional[date] = None
    active: Optional[bool] = None
    read_at: Optional[datetime] = None  # instante da leitura (UTC)
    error: Optional[str] = None

    @property
    def pct(self) -> Optional[float]:
        """Consumo em % do limite diário, ou None se faltar algum dos dois."""
        if self.current is None or not self.limit_day:
            return None
        return 100.0 * self.current / self.limit_day

    def reference_date(self, day: date) -> date:
        """Data a partir da qual o vencimento é contado.

        O e-mail sai minutos depois da leitura, então quem lê conta a partir de HOJE — e
        `day` é o dia ANTERIOR (o relatório cobre o dia fechado). Medir por `day` exibiria
        um dia a mais de prazo e atrasaria o alerta em um dia, os dois na direção errada.
        Em UTC, que é o fuso de `subscription.end`. Cai para `day` se não houve carimbo.
        """
        return self.read_at.date() if self.read_at else day

    def days_to_end(self, day: date) -> Optional[int]:
        """Dias até o vencimento a partir de reference_date (negativo = já vencido)."""
        if self.subscription_end is None:
            return None
        return (self.subscription_end - self.reference_date(day)).days

    def quota_alert(self) -> bool:
        """Consumo PASSOU de QUOTA_ALERT_PCT — estrito: 80,0% cravado não alerta."""
        pct = self.pct
        return pct is not None and pct > QUOTA_ALERT_PCT

    def subscription_alert(self, day: date) -> bool:
        """Faltam MENOS de SUBSCRIPTION_ALERT_DAYS dias (estrito) ou o plano está inativo."""
        if self.active is False:
            return True
        days = self.days_to_end(day)
        return days is not None and days < SUBSCRIPTION_ALERT_DAYS

    def as_log_dict(self, day: date) -> Dict[str, Any]:
        """Forma compacta da leitura p/ a resposta do Cloud Run (e daí p/ o Cloud Logging)."""
        return {
            "current": self.current,
            "limit_day": self.limit_day,
            "pct": round(self.pct, 1) if self.pct is not None else None,
            "plan": self.plan,
            "subscription_end": (
                self.subscription_end.isoformat() if self.subscription_end else None
            ),
            "days_to_end": self.days_to_end(day),
            "alert_quota": self.quota_alert(),
            "alert_subscription": self.subscription_alert(day),
            "error": self.error,
        }


@dataclass
class EodQuotaReading:
    """Leitura de `quota_remaining` mais próxima do reset da cota (00:00 UTC / 21:00
    BRT), escolhida por `select_eod_reading`/`collect_quota_eod` — DE#80.

    `limit_day` vem da leitura `/status` da MESMA execução do resumo (`QuotaInfo`), não
    é medido aqui. Ausente (leitura de `/status` degradada) => `consumed`/`pct` ficam
    None e a seção mostra só o valor bruto de `remaining`.
    """

    read_at: datetime
    remaining: int
    limit_day: Optional[int] = None

    @property
    def consumed(self) -> Optional[int]:
        if self.limit_day is None:
            return None
        return self.limit_day - self.remaining

    @property
    def pct(self) -> Optional[float]:
        consumed = self.consumed
        if consumed is None or not self.limit_day:
            return None
        return 100.0 * consumed / self.limit_day


def _reset_instant_utc(day: date) -> datetime:
    """Instante do reset da cota (00:00 UTC) que cai DENTRO do dia `day` em BRT — ou
    seja, 21:00 BRT do próprio `day`. Fonte: doc oficial da API-Football (reset diário
    às 00:00 UTC — https://www.api-football.com/news/post/how-ratelimit-works).
    """
    local = datetime(day.year, day.month, day.day, 21, 0, tzinfo=SAO_PAULO)
    return local.astimezone(timezone.utc)


def select_eod_reading(
    readings: List[Tuple[Optional[datetime], int]], day: date
) -> Optional[Tuple[datetime, int]]:
    """Escolhe, entre leituras `(timestamp UTC, quota_remaining)`, a mais recente que
    NÃO passa do reset de `day` (21:00 BRT). Leitura depois do reset pertence ao
    próximo "dia da API" e é ignorada — nunca inventa um valor: sem leitura elegível,
    devolve None.
    """
    limite = _reset_instant_utc(day)
    elegiveis = [
        (ts, remaining) for ts, remaining in readings if ts is not None and ts <= limite
    ]
    if not elegiveis:
        return None
    return max(elegiveis, key=lambda par: par[0])


def collect_quota_eod(
    readings: List[Tuple[Optional[datetime], int]],
    day: date,
    limit_day: Optional[int] = None,
) -> Optional[EodQuotaReading]:
    """Empacota `select_eod_reading` num `EodQuotaReading`, já com o `limit_day` da
    leitura `/status` do mesmo dia (para o percentual). None propagado se não houver
    leitura elegível.
    """
    escolhida = select_eod_reading(readings, day)
    if escolhida is None:
        return None
    ts, remaining = escolhida
    return EodQuotaReading(read_at=ts, remaining=remaining, limit_day=limit_day)


def _parse_end(value: Any) -> Optional[date]:
    """ISO 8601 com offset ("2026-08-11T12:21:59+00:00") ou data simples ("2026-08-11")."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        logger.warning(f"subscription.end ilegivel no /status: {value!r}")
        return None


def parse_quota(envelope: Dict[str, Any], read_at: Optional[datetime] = None) -> QuotaInfo:
    """Traduz o envelope do /status em QuotaInfo.

    Nunca levanta: o que não dá para ler vira `error` e a seção sai degradada. Cota
    estourada chega como HTTP 200 + `errors` preenchido (não 429) e cai no primeiro
    branch — o próprio motivo já é a notícia que queremos dar.
    """
    errors = envelope.get("errors")
    if errors:  # list vazia [] (sucesso) é falsy
        return QuotaInfo(read_at=read_at, error=f"API-Football retornou errors: {errors}")

    resp = envelope.get("response")
    reqs = resp.get("requests") if isinstance(resp, dict) else None
    if not isinstance(reqs, dict):
        return QuotaInfo(read_at=read_at, error="resposta do /status sem o bloco `requests`")

    sub = resp.get("subscription")
    sub = sub if isinstance(sub, dict) else {}
    return QuotaInfo(
        current=reqs.get("current"),
        limit_day=reqs.get("limit_day"),
        plan=sub.get("plan"),
        subscription_end=_parse_end(sub.get("end")),
        active=sub.get("active"),
        read_at=read_at,
    )


def collect_quota(client: Any = None) -> QuotaInfo:
    """Consulta o /status (1 chamada/dia). NUNCA levanta.

    O resumo diário é o único canal de alarme que existe no pipeline: ele não pode
    deixar de ser enviado porque a cota não pôde ser lida. Qualquer falha — inclusive o
    serviço subir sem o secret API_FOOTBALL_KEY montado — vira seção degradada com o
    motivo, no mesmo espírito com que o resumo já tolera Logging/Executions indisponíveis.
    """
    read_at = datetime.now(timezone.utc)
    try:
        if client is None:
            client = ApiFootballClient()
        return parse_quota(client.get_status(), read_at=read_at)
    except Exception as e:
        logger.warning(f"Status da API-Football indisponivel, secao de cota degradada: {e}")
        return QuotaInfo(read_at=read_at, error=f"{type(e).__name__}: {e}")


def _fmt_dias(days: int) -> str:
    plural = "s" if abs(days) != 1 else ""
    return f"VENCIDO ha {abs(days)} dia{plural}" if days < 0 else f"{days} dia{plural}"


def _linha(rotulo: str, valor: str, destaque: str, alerta: bool) -> str:
    return (
        "<tr>"
        + cell(rotulo)
        + cell(valor, "right")
        + cell(destaque, "right", RED if alerta else None)
        + "</tr>"
    )


def _consumo_row(
    quota: Optional[QuotaInfo], quota_eod: Optional[EodQuotaReading]
) -> Tuple[str, str, str, bool, Optional[str]]:
    """Escolhe o que a linha de consumo mostra: o total do dia (EOD, DE#80) quando
    disponível — é o número que Victor pede pra decidir cadência de coleta, o dia
    inteiro, não um piso de 3h de madrugada — ou, na ausência dele (o poll de
    fixtures-live não rodou nesse dia), a leitura de madrugada como fallback
    degradado, sinalizada como parcial para não se confundir com o total.

    Devolve (rotulo, consumo_txt, pct_txt, alerta, nota_degradada).
    """
    if quota_eod is not None:
        pct = quota_eod.pct
        pct_txt = f"{pct:.1f}%" if pct is not None else "—"
        consumo_txt = escape(
            f"{quota_eod.consumed} / {quota_eod.limit_day}"
            if quota_eod.limit_day is not None
            else f"restante {quota_eod.remaining}"
        )
        alerta = pct is not None and pct > QUOTA_ALERT_PCT
        rotulo = f"Consumo do dia ({fmt_brt(quota_eod.read_at)} BRT)"
        return rotulo, consumo_txt, pct_txt, alerta, None

    if quota is not None and not quota.error and quota.current is not None:
        pct_txt = f"{quota.pct:.1f}%" if quota.pct is not None else "—"
        consumo_txt = escape(
            f"{quota.current} / {quota.limit_day}" if quota.limit_day else f"{quota.current}"
        )
        rotulo = f"Consumo parcial ({fmt_brt(quota.read_at)} BRT)"
        nota = (
            "Sem leitura de fim de dia hoje (poll de fixtures-live nao rodou perto do "
            "reset) — mostrando so o snapshot de madrugada, que cobre so as primeiras "
            "horas do dia da API."
        )
        return rotulo, consumo_txt, pct_txt, quota.quota_alert(), nota

    return "", "", "", False, None


def build_quota_section(
    quota: Optional[QuotaInfo], day: date, quota_eod: Optional[EodQuotaReading] = None
) -> str:
    """Seção de cota do e-mail.

    Sempre renderiza quando houve tentativa de leitura — degradada, se ela falhou.
    Retorna "" só quando não houve tentativa nenhuma (`quota=None` e `quota_eod=None`).

    A linha de consumo prioriza `quota_eod` (DE#80, total do dia): uma leitura só, sem
    o piso de madrugada ao lado pra não confundir quem lê. `quota_eod` é aditivo e
    independente do estado de `quota` — mesmo se a leitura de madrugada (`/status`)
    falhar ou nem existir, a linha de consumo ainda aparece se o EOD estiver disponível
    (só a linha "Plano" some, porque essa vem só do `/status`).
    """
    if quota is None and quota_eod is None:
        return ""

    titulo = '<h3 style="margin:18px 0 6px">Cota da API-Football</h3>'

    if quota is not None and quota.error and quota_eod is None:
        return (
            titulo
            + f'<p style="margin:0;color:{AMBER};font-size:13px">'
            + f"Leitura indisponivel: {escape(quota.error)}</p>"
        )

    rotulo, consumo_txt, pct_txt, consumo_alerta, nota_degradada = _consumo_row(quota, quota_eod)

    linhas = [_linha(rotulo, consumo_txt, pct_txt, consumo_alerta)] if rotulo else []

    plano_alerta = False
    dias = None
    if quota is not None and not quota.error:
        dias = quota.days_to_end(day)
        if quota.subscription_end is None:
            venc_txt = "—"
        else:
            venc_txt = quota.subscription_end.isoformat()
            if dias is not None:
                venc_txt += f" ({_fmt_dias(dias)})"
        plano_txt = escape(str(quota.plan or "—"))
        if quota.active is False:
            plano_txt += " (INATIVO)"
        plano_alerta = quota.subscription_alert(day)
        linhas.append(_linha("Plano", plano_txt, venc_txt, plano_alerta))

    if not linhas:
        motivo = quota.error if quota is not None and quota.error else "sem leitura disponivel"
        return (
            titulo
            + f'<p style="margin:0;color:{AMBER};font-size:13px">'
            + f"Leitura indisponivel: {escape(motivo)}</p>"
        )

    tabela = (
        '<table style="border-collapse:collapse;font-size:13px"><tbody>'
        + "".join(linhas)
        + "</tbody></table>"
    )

    alertas = []
    if consumo_alerta:
        alertas.append(
            f"ALERTA — consumo em {pct_txt} do limite diario "
            f"(limiar {QUOTA_ALERT_PCT:.0f}%). Agir hoje."
        )
    if plano_alerta:
        # `dias is None` só chega aqui junto de active=False, tratado acima.
        if quota.active is False:
            detalhe = "INATIVO"
        elif dias < 0:
            detalhe = _fmt_dias(dias)  # "VENCIDO ha N dias"
        else:
            detalhe = f"vence em {_fmt_dias(dias)}"
        data_txt = f" ({quota.subscription_end.isoformat()})" if quota.subscription_end else ""
        alertas.append(
            f"ALERTA — plano {detalhe}{data_txt}; limiar {SUBSCRIPTION_ALERT_DAYS} dias. "
            "Renovacao depende de terceiro."
        )
    bloco_alertas = "".join(
        f'<p style="margin:6px 0 0;color:{RED};font-size:13px"><b>{escape(a)}</b></p>'
        for a in alertas
    )

    nota = ""
    if nota_degradada:
        nota = f'<p style="margin:4px 0 0;color:{MUTED};font-size:12px">{escape(nota_degradada)}</p>'
    elif quota is not None and quota.error and quota_eod is not None:
        nota = (
            f'<p style="margin:4px 0 0;color:{AMBER};font-size:12px">'
            f"Leitura de madrugada indisponivel ({escape(quota.error)}) — sem dado de plano/vencimento hoje.</p>"
        )

    return titulo + tabela + bloco_alertas + nota
