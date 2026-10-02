"""Detector de sync concorrente: dois syncs do mesmo (sport, env) ao mesmo tempo (DE#107).

Mesmo papel do detector de atraso (ADR 0002): vigia que roda FORA do sistema que observa,
SOMENTE LEITURA e que não conserta nada. Roda por mão humana, nos dias seguintes ao deploy
da trava e sempre que alguém suspeitar de sobreposição; não é agendado (um vigia hospedado
no GCP morreria junto com o Scheduler/Workflow que ele observa, o argumento da Decisão A do
ADR 0002). Lê dois lugares, pelo `gcloud`, só com `list`/`read`:

1. Cloud Logging do serviço `sync-bq-to-postgres`: de cada requisição que de fato rodou um
   sync sai um intervalo por (sport, env). É a evidência que decide o vermelho.
2. Execuções do `workflow-futebol-sync`: execuções do mesmo workflow que se sobrepõem
   (retry que passa da hora, disparo manual em cima do cron, entrega duplicada do
   Scheduler). Só informativo: uma execução longa que ainda corre quando a seguinte começa
   é esperada; o que importa é se os SYNCS dentro delas se sobrepuseram.

REGRAS (herdadas da versão de diagnóstico de 28/09, que mediu as sobreposições da issue):
- A resposta de uma requisição é `timestamp + httpRequest.latency`. O `timestamp` do log de
  requisição do Cloud Run é a CHEGADA (conferido em 01/10 contra o início da execução do
  Workflows); `receiveTimestamp` é a hora de ingestão do log e engana.
- Só é sync a requisição com "Sync solicitado" do mesmo (sport, env) na MESMA instância logo
  depois da chegada: 401/403/429 nunca entram no app.
- 409 NÃO é sync: é a trava devolvendo "já em andamento" sem tocar em nada. É contado à
  parte, porque é a prova de que a trava está trabalhando.
- Requisição cortada (504 do Cloud Run, 499 quando o cliente desiste) deixa a thread viva: o
  FIM do sync é a próxima linha de fim do mesmo (sport, env) na mesma instância; sem linha de
  fim, a thread morreu com a instância (último log dela). Casar sem a instância inventava
  "órfãs de horas" que eram artefato (a vida real após o corte medida foi de 1 a 708 s).
- "Falha no sync" sai em `textPayload` (stderr), além de `jsonPayload`.

LIMITES: não vê sync fora do serviço (script local apontado para o mesmo banco) e, sem linha de
fim e sem outro log, a órfã é datada pelo último log da instância (pode subestimar).

Tudo aqui é puro, exceto `le_logs` e `le_execucoes_workflow`, que chamam o `gcloud`.
"""
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta

SERVICO = "sync-bq-to-postgres"
WORKFLOW = "workflow-futebol-sync"

# Requisição que o Cloud Run/Workflows cortou: a thread do handler segue rodando.
STATUS_CORTADOS = frozenset({499, 504})
STATUS_OCUPADO_HTTP = 409

# Quanto depois da chegada o "Sync solicitado" ainda pertence à requisição.
JANELA_INICIO = timedelta(seconds=90)
# Tolerância ao casar a linha de fim com a resposta de uma requisição que terminou normal.
TOLERANCIA_FIM = timedelta(seconds=3)

_INICIO = re.compile(r"Sync solicitado sport=(\w+) env=(\w+)")
_FIM = (
    re.compile(r"Sync sport=(\w+) env=(\w+) concluído"),
    re.compile(r"Falha no sync \(sport=(\w+), env=(\w+)\)"),
    re.compile(r"Schema drift detectado \(sport=(\w+), env=(\w+)\)"),
)


@dataclass
class Sync:
    chave: tuple  # (sport, env)
    instancia: str
    chegada: datetime
    resposta: datetime
    status: int
    fim: datetime
    como: str  # "resposta" | "órfã, linha de fim" | "órfã, morreu com a instância"

    @property
    def orfa(self) -> bool:
        return self.status in STATUS_CORTADOS


@dataclass
class ResultadoLogs:
    syncs: list
    barradas: list  # (chave, chegada) das requisições 409: a trava trabalhando


@dataclass
class Execucao:
    nome: str
    inicio: datetime
    fim: datetime
    estado: str


def parse_ts(s: str) -> datetime:
    """RFC 3339 do Cloud Logging/Workflows (nanossegundos) -> datetime UTC (microssegundos)."""
    base, _, frac = s.rstrip("Z").partition(".")
    return datetime.fromisoformat(f"{base}.{(frac + '000000')[:6]}+00:00")


def parse_latencia(s) -> timedelta:
    return timedelta(seconds=float((s or "0s").rstrip("s") or 0))


def chave_da_url(url: str) -> tuple:
    consulta = url.split("?", 1)[-1] if "?" in url else ""
    q = dict(p.split("=", 1) for p in consulta.split("&") if "=" in p)
    # Defaults do handler (cloud_run/sync_bq_to_postgres/main.py).
    return (q.get("sport", "nba"), q.get("env", "prd"))


def _mensagem(entrada: dict) -> str:
    return (entrada.get("jsonPayload") or {}).get("message") or entrada.get("textPayload") or ""


def extrai_syncs(entradas: list) -> ResultadoLogs:
    """Das entradas de log do serviço, os intervalos de sync por (sport, env)."""
    reqs, inicios, fins, ultimo_log = [], [], [], {}
    for e in entradas:
        inst = (e.get("labels") or {}).get("instanceId", "")
        t = parse_ts(e["timestamp"])
        hr = e.get("httpRequest")
        if hr:
            reqs.append(
                {
                    "k": chave_da_url(hr.get("requestUrl", "")),
                    "inst": inst,
                    "chegada": t,
                    "resposta": t + parse_latencia(hr.get("latency")),
                    "status": hr.get("status"),
                }
            )
            continue
        if inst:
            ultimo_log[inst] = max(ultimo_log.get(inst, t), t)
        msg = _mensagem(e)
        m = _INICIO.search(msg)
        if m:
            inicios.append({"k": m.groups(), "inst": inst, "t": t, "usado": False})
            continue
        for rx in _FIM:
            m = rx.search(msg)
            if m:
                fins.append({"k": m.groups(), "inst": inst, "t": t, "usado": False})
                break

    reqs.sort(key=lambda r: r["chegada"])
    inicios.sort(key=lambda x: x["t"])
    fins.sort(key=lambda x: x["t"])

    barradas = [(r["k"], r["chegada"]) for r in reqs if r["status"] == STATUS_OCUPADO_HTTP]

    # Só é sync a requisição que o app registrou na mesma instância; 409 nunca é sync.
    candidatas = []
    for r in reqs:
        if r["status"] == STATUS_OCUPADO_HTTP:
            continue
        for i in inicios:
            if (
                not i["usado"]
                and i["k"] == r["k"]
                and i["inst"] == r["inst"]
                and r["chegada"] <= i["t"] <= r["chegada"] + JANELA_INICIO
            ):
                i["usado"] = True
                candidatas.append(r)
                break

    # Requisições que terminaram sozinhas: o fim é a resposta (e consome a linha de fim dela,
    # para ela não ser casada com uma órfã).
    for r in candidatas:
        if r["status"] in STATUS_CORTADOS:
            continue
        r["fim"], r["como"] = r["resposta"], "resposta"
        for f in fins:
            if (
                not f["usado"]
                and f["k"] == r["k"]
                and f["inst"] == r["inst"]
                and abs(f["t"] - r["resposta"]) <= TOLERANCIA_FIM
            ):
                f["usado"] = True
                break
    # Órfãs: a próxima linha de fim do mesmo alvo e instância depois da chegada.
    for r in candidatas:
        if r["status"] not in STATUS_CORTADOS:
            continue
        for f in fins:
            if (
                not f["usado"]
                and f["k"] == r["k"]
                and f["inst"] == r["inst"]
                and f["t"] > r["chegada"]
            ):
                f["usado"] = True
                r["fim"], r["como"] = f["t"], "órfã, linha de fim"
                break
        else:
            # No mínimo até o corte: a thread estava viva quando a requisição foi cortada.
            r["fim"] = max(ultimo_log.get(r["inst"], r["resposta"]), r["resposta"])
            r["como"] = "órfã, morreu com a instância"

    syncs = [
        Sync(
            chave=r["k"],
            instancia=r["inst"],
            chegada=r["chegada"],
            resposta=r["resposta"],
            status=r["status"],
            fim=r["fim"],
            como=r["como"],
        )
        for r in candidatas
    ]
    return ResultadoLogs(syncs=syncs, barradas=barradas)


def acha_concorrentes(syncs: list) -> list:
    """Pares de syncs do mesmo (sport, env) cujos intervalos se sobrepõem."""
    ordenados = sorted(syncs, key=lambda s: s.chegada)
    pares = []
    for i, a in enumerate(ordenados):
        for b in ordenados[i + 1 :]:
            if b.chave == a.chave and b.chegada < a.fim:
                pares.append((a, b))
    return pares


def sobreposicao_segundos(a: Sync, b: Sync) -> float:
    return (min(a.fim, b.fim) - b.chegada).total_seconds()


def parse_execucoes(itens: list, ate: datetime) -> list:
    """Execuções do `gcloud workflows executions list --format=json`. ACTIVE termina em `ate`."""
    execs = []
    for it in itens:
        inicio = it.get("startTime") or it.get("createTime")
        if not inicio:
            continue
        fim = it.get("endTime")
        execs.append(
            Execucao(
                nome=it.get("name", "").rsplit("/", 1)[-1],
                inicio=parse_ts(inicio),
                fim=parse_ts(fim) if fim else ate,
                estado=it.get("state", ""),
            )
        )
    return execs


def acha_execucoes_sobrepostas(execs: list) -> list:
    ordenadas = sorted(execs, key=lambda e: e.inicio)
    pares = []
    for i, a in enumerate(ordenadas):
        for b in ordenadas[i + 1 :]:
            if b.inicio < a.fim:
                pares.append((a, b))
    return pares


def _hms(d: datetime) -> str:
    return d.strftime("%m-%d %H:%M:%S")


def monta_relatorio(resultado: ResultadoLogs, execs: list | None = None) -> tuple:
    """(texto, codigo): 0 verde, 1 vermelho, 2 sem dado.

    Vermelho = dois syncs reais do mesmo alvo. Sem dado = nenhum sync real na janela (janela
    errada, filtro de log que parou de casar, serviço fora do ar): "nenhum concorrente" seria
    vacuamente verdadeiro, então não é verde.
    """
    syncs = resultado.syncs
    concorrentes = acha_concorrentes(syncs)
    envolvidos = {id(x) for p in concorrentes for x in p}
    linhas = []
    for s in syncs:
        if s.orfa or id(s) in envolvidos:
            vida = (s.fim - s.resposta).total_seconds() if s.orfa else 0
            linhas.append(
                f"{'/'.join(s.chave)}  inst=…{s.instancia[-6:]}  status={s.status}  "
                f"chegou {_hms(s.chegada)}  resposta {_hms(s.resposta)}  fim {_hms(s.fim)}"
                f"  ({s.como}{f', +{vida:.0f}s depois do corte' if vida else ''})"
            )
    for a, b in concorrentes:
        linhas.append(
            f"CONCORRENTE {'/'.join(a.chave)}: {_hms(a.chegada)}..{_hms(a.fim)} x "
            f"{_hms(b.chegada)}..{_hms(b.fim)}  sobreposição {sobreposicao_segundos(a, b):.0f}s"
        )
    n_orfas = sum(1 for s in syncs if s.orfa)
    linhas.append(
        f"requisições de sync={len(syncs)}; cortadas (504/499)={n_orfas}; "
        f"barradas pela trava (409)={len(resultado.barradas)}"
    )
    if execs is not None:
        sob = acha_execucoes_sobrepostas(execs)
        linhas.append(
            f"execuções do {WORKFLOW}: {len(execs)}; sobrepostas entre si: {len(sob)} "
            f"(informativo: o que decide é a sobreposição de syncs acima)"
        )
        for a, b in sob[:20]:
            linhas.append(f"  execução {a.nome[:8]} {_hms(a.inicio)}..{_hms(a.fim)} x {b.nome[:8]} {_hms(b.inicio)}..{_hms(b.fim)}")
    if concorrentes:
        linhas.append(f"VERMELHO: {len(concorrentes)} par(es) de syncs concorrentes")
        return "\n".join(linhas), 1
    if not syncs:
        linhas.append(
            "SEM DADO: nenhum sync real na janela (confira a janela, o filtro de log e se o "
            "serviço rodou); isto NÃO é verde"
        )
        return "\n".join(linhas), 2
    linhas.append("VERDE: nenhum sync concorrente")
    return "\n".join(linhas), 0


# ------------------------------------------------------------------
# Leitura (gcloud, somente list/read)
# ------------------------------------------------------------------
def _gcloud_json(argv: list):
    saida = subprocess.run(argv, capture_output=True, text=True, check=True).stdout
    return json.loads(saida or "[]")


def le_logs(projeto: str, desde: datetime, ate: datetime, limite: int = 100000) -> list:
    filtro = (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{SERVICO}" '
        f'AND timestamp>="{desde.strftime("%Y-%m-%dT%H:%M:%SZ")}" '
        f'AND timestamp<="{ate.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    )
    return _gcloud_json(
        ["gcloud", "logging", "read", filtro, f"--project={projeto}", "--format=json", f"--limit={limite}"]
    )


def le_execucoes_workflow(projeto: str, regiao: str, desde: datetime, ate: datetime) -> list:
    itens = _gcloud_json(
        [
            "gcloud", "workflows", "executions", "list", WORKFLOW,
            f"--location={regiao}", f"--project={projeto}", "--format=json", "--limit=5000",
        ]
    )
    execs = parse_execucoes(itens, ate)
    return [e for e in execs if e.fim >= desde and e.inicio <= ate]
