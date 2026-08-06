"""Cota da API-Football no resumo diário: leitura do /status, limiares e seção HTML.

Por que fica fora de `daily_summary.py`: o resumo importa google-cloud-logging e
google-cloud-workflows, que só existem no requirements do Cloud Run. Aqui moram a
leitura, as duas decisões de alerta e a renderização — todas testáveis sem rede e sem
SDK de GCP (`tests/test_daily_summary_quota.py`). O `daily_summary` só faz a fiação.

⚠️ O que o número significa. `requests.current` do /status é o contador do dia CORRENTE
da API no instante da chamada — não o consumo de um dia fechado. O resumo roda às 00:05
BRT (03:05 UTC), então o valor lido cobre apenas as primeiras horas do dia da API. Por
isso a seção carimba o horário da leitura, e por isso o alerta de consumo vale como
PISO (se disparar às 00:05, o estouro é grave) e não como medida do dia do relatório. O
alerta de vencimento não depende do horário e vale integralmente.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from typing import Any, Callable, Dict, Optional

from src.clients.api_football_client import ApiFootballClient
from src.config import QUOTA_ALERT_PCT, SUBSCRIPTION_ALERT_DAYS
from src.reporting.formatting import AMBER, MUTED, RED, cell, fmt_brt
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

    def days_to_end(self, day: date) -> Optional[int]:
        """Dias até o vencimento, contados a partir do dia do relatório (negativo = vencido)."""
        if self.subscription_end is None:
            return None
        return (self.subscription_end - day).days

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


def collect_quota(
    client: Any = None,
    client_factory: Optional[Callable[[], Any]] = None,
) -> QuotaInfo:
    """Consulta o /status (1 chamada/dia). NUNCA levanta.

    O resumo diário é o único canal de alarme que existe no pipeline: ele não pode
    deixar de ser enviado porque a cota não pôde ser lida. Qualquer falha — inclusive o
    serviço subir sem o secret API_FOOTBALL_KEY montado — vira seção degradada com o
    motivo, no mesmo espírito com que o resumo já tolera Logging/Executions indisponíveis.
    """
    read_at = datetime.now(timezone.utc)
    try:
        if client is None:
            client = (client_factory or ApiFootballClient)()
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


def build_quota_section(quota: Optional[QuotaInfo], day: date) -> str:
    """Seção de cota do e-mail.

    Sempre renderiza quando houve tentativa de leitura — degradada, se ela falhou.
    Retorna "" só quando não houve tentativa nenhuma (`quota=None`).
    """
    if quota is None:
        return ""

    titulo = '<h3 style="margin:18px 0 6px">Cota da API-Football</h3>'

    if quota.error:
        return (
            titulo
            + f'<p style="margin:0;color:{AMBER};font-size:13px">'
            + f"Leitura indisponivel: {escape(quota.error)}</p>"
        )

    pct = quota.pct
    pct_txt = f"{pct:.1f}%" if pct is not None else "—"
    consumo_txt = (
        f"{quota.current} / {quota.limit_day}" if quota.limit_day else f"{quota.current}"
    )

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

    tabela = (
        '<table style="border-collapse:collapse;font-size:13px">'
        "<tbody>"
        + _linha("Consumo do dia (API)", consumo_txt, pct_txt, quota.quota_alert())
        + _linha("Plano", plano_txt, venc_txt, quota.subscription_alert(day))
        + "</tbody></table>"
    )

    alertas = []
    if quota.quota_alert():
        alertas.append(
            f"ALERTA — consumo em {pct_txt} do limite diario "
            f"(limiar {QUOTA_ALERT_PCT:.0f}%). Agir hoje."
        )
    if quota.subscription_alert(day):
        if quota.active is False:
            detalhe = "INATIVO"
        elif dias is None:
            detalhe = "sem data de vencimento legivel"
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

    # Sem este carimbo um contador parcial se parece com o consumo do dia inteiro.
    nota = (
        f'<p style="margin:4px 0 0;color:{MUTED};font-size:12px">'
        f"Contador do dia corrente da API, lido as {fmt_brt(quota.read_at)} BRT — cobre "
        "so as horas ja decorridas do dia da API, nao o dia do relatorio.</p>"
    )

    return titulo + tabela + bloco_alertas + nota
