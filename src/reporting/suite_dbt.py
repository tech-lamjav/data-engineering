"""Seção da suíte dbt (o resto, fora de `tag:guarda`) no e-mail do resumo diário — DE#19.

A fase 4 do `workflow_futebol.yml` roda 36 testes (`tag:guarda`). O projeto `dbt_futebol`
tem 332. Os ~291 restantes — `relationships`, `not_null`, `unique` de todos os marts —
nunca rodavam em produção: existiam só no laptop de quem lembrasse. A fase 5 passou a
rodá-los; esta seção é o outro lado do fio, o consumidor que faz o resultado chegar em
algum lugar que alguém lê.

**POR QUE NÃO BASTA UM `suite_status`.** As guardas se reportam por status porque são todas
`severity: error`: guarda vermelha derruba o job, o conector levanta, o workflow marca
`guardas_status`. A suíte restante NÃO funciona assim. Tudo que ela acusa hoje é
`severity: warn` — os 3 `relationships` de órfãos conhecidos e o baseline estrutural de
cobertura — e `dbt test` **sai com 0 quando só há WARN**. Uma seção que lesse só o status
ficaria verde para sempre e não detectaria nada. É por isso que aqui se lê o LOG:

    Done. PASS=291 WARN=15 ERROR=0 SKIP=0 TOTAL=306

Essa linha é o único lugar onde WARN aparece, e WARN é o estado normal desta fase — daí a
segunda diferença em relação a `guardas.py`: **a seção é renderizada mesmo verde**. Guarda
só aparece quando acende, porque acender é anormal; a suíte é INVENTÁRIO, e um inventário
que some quando está limpo não deixa ninguém perceber que o número de WARN dobrou.

**Como a linha é encontrada.** O workflow emite `suite_execution` (o nome da execução do
Cloud Run Job) no `log_completion`, junto de `suite_status`. Com o nome em mãos é uma
consulta ao Cloud Logging filtrada por execução — sem varrer o job inteiro e sem precisar
adivinhar, pelo formato do log, qual execução era `dbt run`, qual era `dbt snapshot` e qual
era teste (o job `dbt-futebol` é o mesmo para as quatro fases).

Fora de `daily_summary.py` pelo mesmo motivo que `api_quota.py`, `guardas.py` e
`procedencia.py`: dá para montar e testar a seção sem arrastar google-cloud-logging, que só
existe no requirements do Cloud Run. O import do SDK é preguiçoso, dentro do coletor.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Optional

from src.config import GCP_PROJECT_ID
from src.reporting.formatting import AMBER, MUTED, RED, cell as _cell, fmt_brt as _fmt_brt
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Job que roda as quatro fases dbt do workflow futebol.
JOB_DBT = "dbt-futebol"

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
    """Uma execução da fase 5 vista pelo `log_completion` do workflow.

    `execucao` é o nome COMPLETO do recurso (projects/.../executions/dbt-futebol-abc12) ou
    "" — o workflow não consegue nomear a execução quando o job falha (o conector levanta
    antes de atribuir o resultado), e nesse caso o status já diz o que houve.
    """

    quando: Optional[datetime] = None  # UTC
    status: str = "NOT_RUN"
    execucao: str = ""

    @property
    def execucao_curta(self) -> str:
        """dbt-futebol-abc12 — o valor do label do Cloud Logging."""
        return self.execucao.rsplit("/", 1)[-1] if self.execucao else ""


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
    erro_leitura: Optional[str] = None
    execucoes_no_dia: int = 0

    @property
    def alarme(self) -> bool:
        """Vira o token [SUITE] no assunto.

        Só ERROR>=1. WARN NÃO alarma: é o estado permanente da suíte hoje (órfãos conhecidos
        + baseline de cobertura), e um assunto que pisca todo dia treina todo mundo a
        ignorá-lo — a mesma razão pela qual `procedencia.py` não alarma em falha de leitura.
        O número de WARN aparece no corpo justamente para ser comparado com o de ontem.
        """
        if self.contagem is not None:
            return self.contagem.error > 0
        # Sem contagem, o status é o que sobrou: PARTIAL_FAILURE = o job caiu, e job de
        # `dbt test` só cai com ERROR>=1 (WARN sai com 0).
        return self.run is not None and self.run.status == "PARTIAL_FAILURE"


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


def _filtro_execucao(execucao_curta: str) -> str:
    return (
        'resource.type="cloud_run_job" '
        f'AND resource.labels.job_name="{JOB_DBT}" '
        f'AND labels."run.googleapis.com/execution_name"="{execucao_curta}" '
        'AND textPayload:"Done. PASS="'
    )


def _texto(entry) -> str:
    """textPayload do Cloud Run; jsonPayload.message se o log vier estruturado."""
    payload = getattr(entry, "payload", None)
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return str(payload.get("message") or "")
    return ""


def collect_suite(runs, client=None) -> Optional[SuiteInfo]:
    """Lê a contagem da execução mais recente da fase 5. Nunca levanta.

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

    if not run.execucao_curta:
        return info

    try:
        if client is None:
            from google.cloud import logging as gcloud_logging

            client = gcloud_logging.Client(project=GCP_PROJECT_ID)
        for entry in client.list_entries(
            resource_names=[f"projects/{GCP_PROJECT_ID}"],
            filter_=_filtro_execucao(run.execucao_curta),
            page_size=10,
        ):
            contagem = parse_done(_texto(entry))
            if contagem is not None:
                info.contagem = contagem
                break
        if info.contagem is None:
            info.erro_leitura = (
                f"execucao {run.execucao_curta} nao tem linha 'Done. PASS=' no Cloud Logging "
                "(retencao de 30 dias, ou o job caiu antes de fechar)"
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

    if run is not None and run.status == "NOT_RUN":
        cor, titulo = AMBER, "Suite dbt (resto da suite) NAO rodou"
        texto = (
            "A fase 5 nao chegou a executar — o workflow desviou antes dela (extract-fixtures "
            "ou o proprio rebuild do DAG falhou). O detalhe esta na tabela de falhas acima."
        )
    elif c is not None and c.error:
        cor, titulo = RED, "Suite dbt — ERROR>=1"
        texto = (
            "Teste de severidade <code>error</code> vermelho fora de <code>tag:guarda</code>. "
            "NAO derruba o board nem o sync (por design): este e-mail e o canal. Qual teste "
            f"falhou: log do job <code>{escape(JOB_DBT)}</code>, execucao "
            f"<code>{escape(run.execucao_curta or '—')}</code>."
        )
    elif c is not None:
        cor, titulo = MUTED, "Suite dbt (resto da suite, fora de tag:guarda)"
        texto = (
            f"{c.warn} WARN e o estado conhecido da base (orfaos de injuries/standings + "
            "baseline estrutural de cobertura). O numero e para COMPARAR com o de ontem: "
            "WARN subindo e regressao nova, mesmo com ERROR=0."
        )
    elif info.erro_leitura:
        cor, titulo = MUTED, "Suite dbt (resto da suite, fora de tag:guarda)"
        texto = (
            f"Fase rodou com status <code>{escape(run.status if run else '—')}</code>, mas a "
            f"contagem nao pode ser lida: {escape(info.erro_leitura)}"
        )
    else:
        cor, titulo = MUTED, "Suite dbt (resto da suite, fora de tag:guarda)"
        texto = "Fase rodou; contagem indisponivel."

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
