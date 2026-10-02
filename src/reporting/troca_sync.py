"""Carga por troca do sync no resumo diário: fallbacks, falhas de troca, avisos e duração (DE#108).

O serviço `sync-bq-to-postgres` devolve, no corpo da resposta, `summary` (com `fallback_tabelas` e
`trocas`, cada uma com `troca_ms` e `tentativas`), `falhas` (tabelas que não trocaram, com o código
do motivo) e `avisos` (sombra órfã com mais de 2 h). O `workflow_futebol_sync` guarda esses três
campos por ambiente e os emite no log de conclusão (`sync_prd`/`sync_dev`); aqui eles são agregados
e mostrados. Sem isso o fallback era invisível fora do log do Cloud Run e a falha de troca aparecia
como o serviço inteiro, sem o nome da tabela (histórias 9, 20, 27 e 54 da spec #112).

Por que fica fora de `daily_summary.py`: o resumo importa google-cloud-logging e
google-cloud-workflows, que só existem no requirements do Cloud Run. Aqui moram as decisões e a
renderização, testáveis sem rede e sem SDK (`tests/test_daily_summary_troca_sync.py`). Este módulo
NÃO importa `src.sync`: o serviço do resumo declara só `src/reporting/` no manifesto de procedência
e não tem psycopg.

Sem token novo no assunto: a falha de troca já vira `[FALHAS]` (o workflow termina em
PARTIAL_FAILURE) e fallback/aviso não são falha de workflow, são informação para o dono do
data-engineering e para o Victor (uma view nova sobre tabela sincronizada manda a tabela para o
fallback). Com a troca desligada (lançamento escuro) nada chega aqui e a seção é omitida: o e-mail
de antes fica intacto.

Tudo o que vem do log é tratado como não confiável: bloco ausente, nulo, de outro tipo ou com
itens malformados é ignorado, nunca derruba a coleta do resumo.
"""
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from src.reporting.formatting import AMBER, MUTED, RED, cell, fmt_brt

AMBIENTES = ("prd", "dev")

# (timestamp UTC do log de conclusão, ambiente, bloco `{summary, falhas, avisos}`)
Registro = Tuple[Optional[datetime], str, Any]


@dataclass
class TrocaTabela:
    """Cargas por troca (ou staged) de uma tabela num ambiente, no dia."""

    cargas: int = 0
    soma_ms: float = 0.0
    max_ms: float = 0.0
    max_tentativas: int = 0

    @property
    def media_ms(self) -> float:
        return self.soma_ms / self.cargas if self.cargas else 0.0


@dataclass
class FalhaTroca:
    quando: datetime
    env: str
    table: str
    motivo: str


@dataclass
class TrocaInfo:
    trocas: Dict[Tuple[str, str], TrocaTabela] = field(default_factory=dict)
    fallbacks: Dict[Tuple[str, str], int] = field(default_factory=dict)  # cargas em fallback
    falhas: List[FalhaTroca] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)


def _mapa(valor: Any) -> Dict[str, Any]:
    return valor if isinstance(valor, dict) else {}


def _lista(valor: Any) -> list:
    return valor if isinstance(valor, list) else []


def _numero(valor: Any) -> Optional[float]:
    if isinstance(valor, bool):
        return None
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def blocos_do_log(payload: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """[(ambiente, bloco)] do log de conclusão; ausente, nulo ou não-mapa não conta."""
    out = []
    for env in AMBIENTES:
        bloco = payload.get(f"sync_{env}")
        if isinstance(bloco, dict):
            out.append((env, bloco))
    return out


def nomes_das_falhas(blocos: List[Tuple[str, Any]]) -> str:
    """'tabela (motivo), tabela (motivo)' das falhas de troca, para o detalhe do parcial."""
    partes = []
    for _, bloco in blocos:
        for f in _lista(_mapa(bloco).get("falhas")):
            f = _mapa(f)
            if f.get("table"):
                partes.append(f"{f['table']} ({f.get('motivo') or '?'})")
    return ", ".join(partes)


def collect_troca(registros: List[Registro]) -> Optional[TrocaInfo]:
    """Agrega os registros do dia. None = nada a relatar (troca desligada): seção omitida.

    Nunca levanta. Falha sem horário é descartada (sem horário não há como ordená-la no e-mail).
    """
    info = TrocaInfo()
    for quando, env, bloco in registros:
        bloco = _mapa(bloco)
        resumo = _mapa(bloco.get("summary"))
        for t in _lista(resumo.get("trocas")):
            t = _mapa(t)
            ms, tentativas = _numero(t.get("troca_ms")), _numero(t.get("tentativas"))
            if not t.get("table") or ms is None:
                continue
            acc = info.trocas.setdefault((env, t["table"]), TrocaTabela())
            acc.cargas += 1
            acc.soma_ms += ms
            acc.max_ms = max(acc.max_ms, ms)
            acc.max_tentativas = max(acc.max_tentativas, int(tentativas or 1))
        for nome in _lista(resumo.get("fallback_tabelas")):
            if isinstance(nome, str) and nome:
                info.fallbacks[(env, nome)] = info.fallbacks.get((env, nome), 0) + 1
        for f in _lista(bloco.get("falhas")):
            f = _mapa(f)
            if quando is not None and f.get("table"):
                info.falhas.append(FalhaTroca(quando, env, str(f["table"]), str(f.get("motivo") or "?")))
        for aviso in _lista(bloco.get("avisos")):
            if isinstance(aviso, str) and aviso and aviso not in info.avisos:
                info.avisos.append(aviso)
    if not (info.trocas or info.fallbacks or info.falhas or info.avisos):
        return None
    info.falhas.sort(key=lambda f: f.quando)
    return info


def build_troca_section(info: Optional[TrocaInfo]) -> str:
    """Seção do e-mail. `info=None` omite (mantém o e-mail de antes)."""
    if info is None:
        return ""

    titulo = '<h3 style="margin:18px 0 6px">Carga por troca do sync</h3>'
    n_cargas = sum(t.cargas for t in info.trocas.values())
    resumo = (
        f'<p style="margin:0 0 6px;color:{MUTED};font-size:13px">'
        + escape(
            f"{n_cargas} carga(s) por troca no dia · {len(info.fallbacks)} tabela(s) em fallback · "
            f"{len(info.falhas)} falha(s) de troca"
        )
        + "</p>"
    )

    tabela = ""
    if info.trocas:
        cab = "".join(
            f'<th style="padding:6px 10px;border:1px solid #ddd;background:#f6f8fa;text-align:{al}">{lb}</th>'
            for lb, al in [
                ("Ambiente", "left"), ("Tabela", "left"), ("Cargas", "right"),
                ("Troca media (ms)", "right"), ("Troca max (ms)", "right"), ("Tentativas max", "right"),
            ]
        )
        linhas = "".join(
            "<tr>"
            + cell(escape(env))
            + cell(escape(nome))
            + cell(t.cargas, "right")
            + cell(f"{t.media_ms:.1f}", "right")
            + cell(f"{t.max_ms:.1f}", "right")
            + cell(t.max_tentativas, "right", AMBER if t.max_tentativas > 1 else None)
            + "</tr>"
            for (env, nome), t in sorted(info.trocas.items())
        )
        tabela = (
            '<table style="border-collapse:collapse;font-size:13px">'
            f"<thead><tr>{cab}</tr></thead><tbody>{linhas}</tbody></table>"
        )

    falhas = "".join(
        f'<p style="margin:6px 0 0;color:{RED};font-size:13px"><b>'
        + escape(
            f"Falha de troca {fmt_brt(f.quando)} BRT, {f.env}: {f.table} ({f.motivo}). A tabela "
            f"ficou com o dado anterior e o estado nao avancou."
        )
        + "</b></p>"
        for f in info.falhas
    )
    fallbacks = "".join(
        f'<p style="margin:6px 0 0;color:{AMBER};font-size:13px">'
        + escape(
            f"Fallback: {nome} ({env}) foi carregada no lugar {n} vez(es) por ter dependente "
            f"(view, politica de outra tabela, funcao...). Avisar quem criou o objeto."
        )
        + "</p>"
        for (env, nome), n in sorted(info.fallbacks.items())
    )
    avisos = "".join(
        f'<p style="margin:6px 0 0;color:{AMBER};font-size:13px">{escape(a)}</p>' for a in info.avisos
    )
    return titulo + resumo + tabela + falhas + fallbacks + avisos
