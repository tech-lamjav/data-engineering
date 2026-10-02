"""Tamanho do Postgres de DEV no resumo diário: última leitura do dia, limiar e seção HTML (DE#106).

O DEV é o projeto free do Supabase, com teto de 500 MB. Acima dele o projeto entra em somente
leitura depois de uma carência e o passe DEV do sync (e os crons do staging que gravam) param.
Nada avisava a aproximação. O sync mede o tamanho ao fim do passe DEV (`src/sync/tamanho_dev.py`),
o workflow o põe no log de conclusão (`dev_size_mb`) e esta seção o mostra.

Por que fica fora de `daily_summary.py`: o resumo importa google-cloud-logging e
google-cloud-workflows, que só existem no requirements do Cloud Run. Aqui moram as decisões e a
renderização, testáveis sem rede e sem SDK (`tests/test_daily_summary_tamanho_dev.py`). Este
módulo NÃO importa `src.sync`: o serviço do resumo declara só `src/reporting/` no manifesto de
procedência e não tem psycopg.

Por que o limiar mora aqui e não em `src/config.py`: o `config.py` entra no carimbo de
procedência dos 29 serviços (ADR 0001); editá-lo derivaria a frota inteira.

Três estados, e o terceiro não é um alerta nem um silêncio:
- leitura <= 450 MB: linha informativa, sem alerta;
- leitura > 450 MB (estrito: 450,0 cravado não alerta): alerta no corpo e `[DEV]` no assunto;
- sem leitura no dia (sync DEV abortado, em erro, medição sem permissão, nenhum sync na janela):
  seção DEGRADADA em âmbar, sem token no assunto. "Não medido" não é "está bem", e sumir a seção
  esconderia justamente o dia em que o sync não terminou.

A leitura mostrada é a ÚLTIMA do dia, como na seção de cota: diz onde o DEV terminou o dia.
"""
import math
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from src.reporting.formatting import AMBER, MUTED, RED, cell, fmt_brt

# Teto do plano free do Supabase e limiar de alerta (spec DE#106: alerta acima de 450 MB).
DEV_TETO_MB = 500.0
DEV_ALERTA_MB = 450.0


@dataclass
class DevSizeReading:
    """Uma leitura do tamanho do DEV: soma dos bancos do cluster, em MiB."""

    read_at: datetime  # UTC, do log de conclusão
    size_mb: float

    @property
    def pct(self) -> float:
        """% do teto de 500 MB."""
        return 100.0 * self.size_mb / DEV_TETO_MB

    @property
    def alarme(self) -> bool:
        """Passou de DEV_ALERTA_MB, estrito."""
        return self.size_mb > DEV_ALERTA_MB


@dataclass
class DevSizeInfo:
    """Estado do dia. `reading` None = sem leitura no dia (seção degradada)."""

    reading: Optional[DevSizeReading] = None

    @property
    def alarme(self) -> bool:
        return self.reading is not None and self.reading.alarme

    def as_log_dict(self) -> Dict[str, Any]:
        """Forma compacta p/ a resposta do Cloud Run (e daí p/ o Cloud Logging)."""
        r = self.reading
        return {
            "read_at": r.read_at.isoformat() if r else None,
            "size_mb": r.size_mb if r else None,
            "pct": round(r.pct, 1) if r else None,
            "alarme": self.alarme,
        }


def _valida(valor: Any) -> Optional[float]:
    """Número finito ou None. Booleano não é número aqui (float(True) == 1.0)."""
    if isinstance(valor, bool):
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def collect_dev_size(readings: List[Tuple[Optional[datetime], float]]) -> DevSizeInfo:
    """Escolhe, entre as leituras `(timestamp UTC, dev_size_mb)` do dia, a mais recente.

    Nunca levanta e nunca inventa: leitura sem horário é descartada (sem horário não há como
    dizer qual é a última) e valor ilegível ou não finito também. Sem leitura válida o estado é
    `DevSizeInfo(reading=None)`.
    """
    validas = []
    for quando, valor in readings:
        numero = _valida(valor)
        if quando is None or numero is None:
            continue
        validas.append((quando, numero))
    if not validas:
        return DevSizeInfo(reading=None)
    quando, numero = max(validas, key=lambda par: par[0])
    return DevSizeInfo(reading=DevSizeReading(read_at=quando, size_mb=numero))


def build_dev_size_section(info: Optional[DevSizeInfo]) -> str:
    """Seção do e-mail. `info=None` omite (mantém o e-mail de antes); com `info` sempre
    renderiza, degradada quando não houve leitura no dia."""
    if info is None:
        return ""

    titulo = '<h3 style="margin:18px 0 6px">Tamanho do DEV (Supabase free, teto de 500 MB)</h3>'

    r = info.reading
    if r is None:
        return (
            titulo
            + f'<p style="margin:0;color:{AMBER};font-size:13px">'
            + "Sem leitura do tamanho do DEV neste dia (o passe DEV do sync nao terminou, "
            + "falhou ou nao conseguiu medir). Nao medido nao quer dizer abaixo do limiar: "
            + "conferir o painel do Supabase.</p>"
        )

    cor = RED if r.alarme else None
    linha = (
        "<tr>"
        + cell(f"Leitura de {fmt_brt(r.read_at)} BRT")
        + cell(escape(f"{r.size_mb:.1f} MB"), "right", cor)
        + cell(escape(f"{r.pct:.1f}% do teto de {DEV_TETO_MB:.0f} MB"), "right", cor)
        + "</tr>"
    )
    tabela = '<table style="border-collapse:collapse;font-size:13px"><tbody>' + linha + "</tbody></table>"

    alerta = ""
    if r.alarme:
        alerta = (
            f'<p style="margin:6px 0 0;color:{RED};font-size:13px"><b>'
            + escape(
                f"ALERTA — DEV em {r.size_mb:.1f} MB, acima do limiar de {DEV_ALERTA_MB:.0f} MB "
                f"(teto {DEV_TETO_MB:.0f} MB). Acima do teto o projeto entra em somente leitura "
                f"e o passe DEV do sync para de gravar. Agir hoje."
            )
            + "</b></p>"
        )

    nota = (
        f'<p style="margin:4px 0 0;color:{MUTED};font-size:12px">'
        "Soma de todos os bancos do cluster, medida ao fim do ultimo passe DEV do sync do dia."
        "</p>"
    )
    return titulo + tabela + alerta + nota
