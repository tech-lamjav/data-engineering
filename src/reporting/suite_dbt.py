"""Seção da suíte dbt (o resto, fora de `tag:guarda`) no e-mail do resumo diário — DE#19.

O projeto `dbt_futebol` tem 332 data tests. A fase 4 do `workflow_futebol.yml` roda 36
(`tag:guarda`) e 5 são da medição da task [F] (`tag:taskf`, dataset que produção não lê).
Os 291 restantes — `relationships`, `not_null`, `unique` de todos os marts — nunca rodavam
em produção: existiam só no laptop de quem lembrasse. A fase 5 passou a rodá-los; esta
seção é o outro lado do fio, o consumidor que faz o resultado chegar onde alguém lê.

(A fase reporta **305** nós, não 291: `dbt test` roda também os 14 **unit tests**, que
`dbt ls --resource-type test` não conta.)

**POR QUE NÃO BASTA UM `suite_status`.** As guardas se reportam por status porque são todas
`severity: error`: guarda vermelha derruba o job, o conector levanta, o workflow marca
`guardas_status`. A suíte restante NÃO funciona assim. Tudo que ela acusa hoje é
`severity: warn` — os 3 `relationships` de órfãos conhecidos e o baseline estrutural de
cobertura — e `dbt test` **sai com 0 quando só há WARN**. Uma seção que lesse só o status
ficaria verde para sempre e não detectaria nada. É por isso que aqui se lê o LOG:

    Done. PASS=295 WARN=10 ERROR=0 SKIP=0 NO-OP=0 TOTAL=305

Essa linha é o único lugar onde WARN aparece, e WARN é o estado normal desta fase — daí a
segunda diferença em relação a `guardas.py`: **a seção é renderizada mesmo verde**. Guarda
só aparece quando acende, porque acender é anormal; a suíte é INVENTÁRIO, e um inventário
que some quando está limpo não deixa ninguém perceber que o número de WARN dobrou.

**Como a execução certa é encontrada — e por que não pelo workflow.** O job `dbt-futebol` é o
MESMO nas quatro fases (`dbt run`, `dbt snapshot`, guardas, suíte), então achar "a linha
`Done.` do dia" no log do job não basta: é preciso saber de QUAL execução. A via óbvia seria
o workflow emitir o nome da execução — e ela **não funciona**: medido em 2026-08-21, o
`<result>.name` do conector `googleapis.run.v2...jobs.run` é o nome da **operação**
(`.../operations/<uuid>`), enquanto o label do Cloud Logging é o nome da **execução**
(`dbt-futebol-xxxxx`). São identificadores diferentes e um não deriva do outro.

O que funciona é perguntar à Cloud Run Admin API v2 quais execuções o job teve na janela do
dia: o recurso `Execution` **carrega os args daquela execução**, inclusive os que vieram de
`containerOverrides`. Daí dá para separar a fase 5 (`dbt test` + `--exclude`) da fase 4
(`dbt test` + `--select tag:guarda`) sem heurística. Bônus: funciona também quando o job
FALHOU, caso em que o workflow não teria nome de execução nenhum para dar.

Fora de `daily_summary.py` pelo mesmo motivo que `api_quota.py`, `guardas.py` e
`procedencia.py`: dá para montar e testar a seção sem arrastar google-cloud-logging, que só
existe no requirements do Cloud Run. O import do SDK é preguiçoso, dentro do coletor.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

import requests

from src.config import GCP_PROJECT_ID
from src.reporting.formatting import AMBER, MUTED, RED, cell as _cell, fmt_brt as _fmt_brt
from src.reporting.procedencia import GCP_REGION, TIMEOUT, token_gcp
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Job que roda as quatro fases dbt do workflow futebol.
JOB_DBT = "dbt-futebol"

# ~60-110 execuções/dia (medido 2026-08-21). Uma página de 200 cobre um dia inteiro com
# folga; as páginas extras existem só para o dia em que algo dispara o job em rajada.
PAGINA = 200
MAX_PAGINAS = 5

# A linha de fechamento do dbt. Lida em DOIS passos, e não com um regex posicional único, por
# uma razão medida: o dbt 1.11 emite `Done. PASS=295 WARN=10 ERROR=0 SKIP=0 NO-OP=0 TOTAL=305`
# — o campo `NO-OP` é novo e fica ENTRE `SKIP` e `TOTAL`. Um regex que exigisse os campos
# adjacentes já teria quebrado nessa versão, calado (a seção viraria "contagem indisponivel"
# sem ninguém entender por quê). Aqui a âncora é só `Done.`; os campos são pares chave=valor.
DONE_RE = re.compile(r"Done\.\s+((?:[A-Z][A-Z-]*=\d+\s*)+)")
CAMPO_RE = re.compile(r"([A-Z][A-Z-]*)=(\d+)")

# Piso de ordenação p/ execução sem timestamp.
_EPOCA = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class SuiteRun:
    """Uma passagem pela fase 5, vista pelo `log_completion` do workflow.

    É só `quando` + `status`: o workflow não tem como dar o nome da execução do job (ver o
    docstring do módulo). Quem acha a execução é `encontra_execucao`.
    """

    quando: Optional[datetime] = None  # UTC
    status: str = "NOT_RUN"


@dataclass
class ContagemDbt:
    """A linha `Done.` de uma execução."""

    passou: int
    warn: int
    error: int
    skip: int
    total: int


@dataclass
class SuiteInfo:
    """O que a seção precisa: a execução mais recente do dia e o que ela contou."""

    run: Optional[SuiteRun] = None
    contagem: Optional[ContagemDbt] = None
    execucao: Optional[str] = None  # nome curto: dbt-futebol-abc12
    erro_leitura: Optional[str] = None
    execucoes_no_dia: int = 0

    @property
    def vermelha(self) -> bool:
        """A fase acusou alguma coisa: `ERROR>=1` no dbt, ou o job caindo por outro motivo.

        O status conta quando não há contagem: o `except` da fase 5 pega `ERROR>=1` **e**
        também OOM, timeout e falha de imagem — não presumir que só o primeiro chega aqui.
        """
        if self.contagem is not None:
            return self.contagem.error > 0
        return self.run is not None and self.run.status == "PARTIAL_FAILURE"

    @property
    def alarme(self) -> bool:
        """Vira o token [SUITE] no assunto.

        Só vermelha. WARN NÃO alarma: é o estado permanente da suíte hoje (órfãos conhecidos
        + baseline de cobertura), e um assunto que pisca todo dia treina todo mundo a
        ignorá-lo — a mesma razão pela qual `procedencia.py` não alarma em falha de leitura.
        O número de WARN aparece no corpo justamente para ser comparado com o de ontem.
        """
        return self.vermelha


def parse_done(texto: str) -> Optional[ContagemDbt]:
    """Extrai a linha `Done. PASS=...` de um trecho de log. None se não houver.

    Campo que o dbt deixar de emitir vira 0 e campo novo é ignorado — a linha continua
    legível numa versão futura em vez de sumir. `PASS` é o único obrigatório: sem ele não é
    a linha de fechamento de um `dbt test`.
    """
    m = DONE_RE.search(texto or "")
    if not m:
        return None
    campos = {k: int(v) for k, v in CAMPO_RE.findall(m.group(1))}
    if "PASS" not in campos:
        return None
    return ContagemDbt(
        passou=campos.get("PASS", 0),
        warn=campos.get("WARN", 0),
        error=campos.get("ERROR", 0),
        skip=campos.get("SKIP", 0),
        total=campos.get("TOTAL", 0),
    )


def e_fase5(args) -> bool:
    """A execução é a da fase 5?

    `dbt test` + `--exclude` a distingue da fase 4 (`dbt test` + `--select tag:guarda`) e das
    fases de `run`/`snapshot`. Não casa o valor do `--exclude`: mudar as tags excluídas é
    mexer só no YAML, e um matcher literal apagaria a seção calado no dia da mudança.

    ⚠️ O discriminador é a PRESENÇA de `--exclude`, e ele só é único enquanto a fase 4
    continuar sem `--exclude` — nos dois workflows que a rodam (futebol e odds). No dia em
    que alguém acrescentar um `--exclude` à seleção das guardas, esta seção passa a reportar
    a execução errada **em silêncio**. Se isso acontecer, o discriminador tem de virar a
    presença de `--select tag:guarda` (fase 4) versus a ausência dela.
    """
    args = list(args or [])
    return args[:2] == ["dbt", "test"] and "--exclude" in args


def _iso_para_utc(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        # A API devolve nanossegundos; `fromisoformat` só aceita micro.
        limpo = re.sub(r"(\.\d{6})\d+", r"\1", iso.replace("Z", "+00:00"))
        return datetime.fromisoformat(limpo).astimezone(timezone.utc)
    except ValueError:
        return None


def encontra_execucao(inicio, fim, sessao=None) -> tuple:
    """Acha a execução MAIS RECENTE da fase 5 na janela. Devolve (nome_curto, quando).

    Nunca levanta: devolve (None, None) e o motivo em log. Ver o docstring do módulo para
    por que a execução é descoberta aqui e não vem do workflow.
    """
    if not GCP_PROJECT_ID:
        logger.warning("Secao da suite degradada: GCP_PROJECT_ID nao configurado")
        return None, None

    sessao = sessao or requests
    url = (
        f"https://run.googleapis.com/v2/projects/{GCP_PROJECT_ID}"
        f"/locations/{GCP_REGION}/jobs/{JOB_DBT}/executions"
    )
    headers = {"Authorization": f"Bearer {token_gcp()}"}
    token_pagina = None
    for _ in range(MAX_PAGINAS):
        params = {"pageSize": PAGINA}
        if token_pagina:
            params["pageToken"] = token_pagina
        resp = sessao.get(url, headers=headers, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        doc = resp.json()
        for ex in doc.get("executions") or []:
            quando = _iso_para_utc(ex.get("createTime"))
            if quando is None:
                continue
            # A API devolve da mais recente p/ a mais antiga: passou do início da janela,
            # não há mais nada a ver.
            if quando < inicio:
                return None, None
            if quando >= fim:
                continue
            containers = (ex.get("template") or {}).get("containers") or [{}]
            if e_fase5(containers[0].get("args")):
                return ex["name"].rsplit("/", 1)[-1], quando
        token_pagina = doc.get("nextPageToken")
        if not token_pagina:
            break
    return None, None


def _filtro_execucao(execucao_curta: str, quando: Optional[datetime]) -> str:
    """Filtro do Cloud Logging para a linha `Done.` de UMA execução.

    Limitado no tempo de propósito: o resumo roda ~00:05 BRT para o dia ANTERIOR, então a
    execução alvo pode ter >24h e uma consulta sem limite varreria os 30 dias de retenção.
    A janela é generosa (±1 dia) porque o custo é a varredura, não a precisão — o label da
    execução já é único.
    """
    filtro = (
        'resource.type="cloud_run_job" '
        f'AND resource.labels.job_name="{JOB_DBT}" '
        f'AND labels."run.googleapis.com/execution_name"="{execucao_curta}" '
        'AND textPayload:"Done. PASS="'
    )
    if quando is not None:
        inicio = quando - timedelta(days=1)
        fim = quando + timedelta(days=1)
        filtro += f' AND timestamp>="{inicio.isoformat()}" AND timestamp<"{fim.isoformat()}"'
    return filtro


def _texto(entry) -> str:
    """textPayload do Cloud Run; jsonPayload.message se o log vier estruturado."""
    payload = getattr(entry, "payload", None)
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return str(payload.get("message") or "")
    return ""


def collect_suite(runs, inicio, fim, client=None, sessao=None) -> Optional[SuiteInfo]:
    """Lê a contagem da execução mais recente da fase 5 na janela. Nunca levanta.

    `runs` é a lista de `SuiteRun` do dia (uma por execução do workflow futebol). Retorna
    None quando a fase nunca apareceu — workflow antigo, ainda sem o campo no
    `log_completion` —, e nesse caso a seção some do e-mail em vez de mentir "não rodou".
    """
    if not runs:
        return None

    # A mais recente é a que descreve o estado atual do mart; as anteriores só contam.
    # Timestamp ausente ordena no começo (o mesmo idioma de `daily_summary.build_html`) —
    # comparar None com None levantaria TypeError e derrubaria o resumo inteiro.
    ordenadas = sorted(runs, key=lambda r: r.quando or _EPOCA)
    run = ordenadas[-1]
    info = SuiteInfo(run=run, execucoes_no_dia=len(runs))

    if run.status == "NOT_RUN":
        return info

    try:
        info.execucao, quando = encontra_execucao(inicio, fim, sessao=sessao)
        if info.execucao is None:
            info.erro_leitura = (
                f"nenhuma execucao de `dbt test --exclude` do job {JOB_DBT} na janela do dia"
            )
            return info

        if client is None:
            from google.cloud import logging as gcloud_logging

            client = gcloud_logging.Client(project=GCP_PROJECT_ID)
        # DESCENDENTE de propósito: com `maxRetries=1` no job, uma execução que falha e
        # repete tem DUAS linhas `Done.`, e a que descreve o resultado final é a última.
        # Ascendente (o default) reportaria a tentativa que falhou de uma execução que
        # terminou verde — falso `[SUITE]`.
        for entry in client.list_entries(
            resource_names=[f"projects/{GCP_PROJECT_ID}"],
            filter_=_filtro_execucao(info.execucao, quando),
            order_by="timestamp desc",
            page_size=10,
        ):
            contagem = parse_done(_texto(entry))
            if contagem is not None:
                info.contagem = contagem
                break
        if info.contagem is None:
            info.erro_leitura = (
                f"execucao {info.execucao} nao tem linha 'Done. PASS=' no Cloud Logging "
                "(o job caiu antes de fechar, ou a retencao de 30 dias ja passou)"
            )
    except Exception as e:
        logger.warning(f"Contagem da suite dbt indisponivel: {e}")
        info.erro_leitura = f"{type(e).__name__}: {e}"
    return info


def build_suite_section(info: Optional[SuiteInfo]) -> str:
    """Seção da suíte. `None` omite a seção (mantém o e-mail de antes intacto).

    Renderiza mesmo quando verde — ver o docstring do módulo: aqui o valor é o inventário,
    não o alarme.
    """
    if info is None:
        return ""

    run = info.run
    c = info.contagem

    # `onde` fecha o unico caminho de acao que existe: a secao nunca diz QUAL teste falhou.
    onde = (
        f"Qual teste falhou: log do job <code>{escape(JOB_DBT)}</code>"
        + (f", execucao <code>{escape(info.execucao)}</code>" if info.execucao else "")
        + "."
    )

    if run is not None and run.status == "NOT_RUN":
        cor, titulo = AMBER, "Suite dbt (resto da suite) NAO rodou"
        texto = (
            "A fase 5 nao chegou a executar — o workflow desviou antes dela (extract-fixtures "
            "falhou, o rebuild do DAG falhou, ou alguma extracao deixou o workflow em "
            "PARTIAL_FAILURE). O detalhe esta na tabela de falhas acima."
        )
    # VERMELHA vem ANTES de qualquer caso cinza. Sem isto, o dia em que o job cai sem deixar
    # linha `Done.` legivel (OOM, timeout, imagem) cairia no ramo generico de "contagem
    # indisponivel" e sairia CINZA — assunto com [SUITE] e corpo dizendo que nao ha nada
    # para ver. Vermelho sem contagem ainda diz onde olhar, que e a acao possivel.
    elif info.vermelha:
        cor, titulo = RED, "Suite dbt — VERMELHA"
        detalhe = (
            f"<code>ERROR={c.error}</code> de {c.total} testes."
            if c is not None
            else "O job da fase 5 caiu (<code>ERROR&gt;=1</code> no dbt, ou OOM/timeout/imagem)"
            + (f": {escape(info.erro_leitura)}" if info.erro_leitura else ".")
        )
        texto = (
            f"{detalhe} Teste de severidade <code>error</code> fora de <code>tag:guarda</code>, "
            "ou o proprio job. NAO derruba o board nem o sync (por design): este e-mail e o "
            f"canal. {onde}"
        )
    elif c is not None:
        cor, titulo = MUTED, "Suite dbt (resto da suite, fora de tag:guarda)"
        texto = (
            f"{c.warn} WARN e o estado conhecido da base (orfaos de injuries/standings + "
            "baseline estrutural de cobertura). O numero e para COMPARAR com o de ontem: "
            "WARN subindo e regressao nova, mesmo com ERROR=0."
        )
    else:
        # Fase verde mas contagem ilegivel: degrada como `procedencia.py` — diz o motivo e
        # nao alarma, porque o alarme aqui seria sobre a LEITURA, nao sobre os dados.
        cor, titulo = MUTED, "Suite dbt (resto da suite, fora de tag:guarda)"
        motivo = escape(info.erro_leitura) if info.erro_leitura else "motivo desconhecido"
        texto = f"A fase rodou sem ERROR, mas a contagem nao pode ser lida: {motivo}. {onde}"

    if c is not None:
        celulas = [
            ("PASS", c.passou, "#1a7f37"),
            ("WARN", c.warn, AMBER if c.warn else None),
            ("ERROR", c.error, RED if c.error else None),
            ("SKIP", c.skip, None),
            ("TOTAL", c.total, None),
        ]
        cabecalho = "".join(
            '<th style="padding:6px 10px;border:1px solid #ddd;background:#f6f8fa;'
            f'text-align:right">{rotulo}</th>'
            for rotulo, _, _ in celulas
        )
        corpo = "".join(_cell(valor, "right", cor_valor) for _, valor, cor_valor in celulas)
        tabela = (
            '<table style="border-collapse:collapse;font-size:13px">'
            f"<thead><tr>{cabecalho}</tr></thead><tbody><tr>{corpo}</tr></tbody></table>"
        )
    else:
        tabela = ""

    rodape = ""
    if run is not None and run.quando is not None:
        rodape = (
            f'<p style="margin:6px 0 0;color:{MUTED};font-size:12px">'
            f"Ultima execucao {_fmt_brt(run.quando)} BRT"
            f"{f' · {info.execucoes_no_dia} execucoes no dia' if info.execucoes_no_dia > 1 else ''}"
            "</p>"
        )

    return (
        f'<h3 style="margin:18px 0 6px;color:{cor}">{titulo}</h3>'
        f'<p style="margin:0 0 8px;color:{MUTED};font-size:13px">{texto}</p>'
        f"{tabela}{rodape}"
    )
