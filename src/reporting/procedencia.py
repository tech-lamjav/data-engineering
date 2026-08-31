"""Seção de procedência das imagens dbt no e-mail do resumo diário.

Os modelos dbt rodam de uma imagem Docker pré-buildada, não do master. Quando alguém
mergeia e esquece de rebuildar, produção segue rodando código velho — e a fase de guardas
dbt **não acusa**, porque ela roda da MESMA imagem derivada. Foi assim que o fix do de-vig
ficou 2 dias fora de produção (perdeu o build por 70 segundos) e o `dbt_nba` ficou ~6
semanas atrás do master. Ver `analytics-engineering/docs/adr/0001`.

O alarme primário é o detector horário no GitHub Actions do repo `analytics-engineering`,
que roda FORA da imagem. Esta seção é o segundo canal: o e-mail diário.

DUAS LEITURAS, E POR QUÊ:

1. **Carimbo de cada job**, pela Cloud Run Admin API v2. Dá o valor concreto que um operador
   precisa para agir (qual hash está em produção, de qual commit, desde quando).
2. **Veredito do detector**, pela API pública do GitHub Actions. O carimbo sozinho não
   permite JULGAR daqui: o hash esperado é calculado da árvore do `analytics-engineering`,
   que este repo não tem. Quem já fez essa conta é o detector — então lemos a conclusão dele
   em vez de refazer o cálculo. Repo público, GET sem token.

A leitura 2 também vigia o vigia: se o detector parou de rodar (workflow agendado em repo
público é desativado após 60 dias sem atividade), o silêncio dele vira notícia aqui.

Fora de `daily_summary.py` pelo mesmo motivo que `api_quota.py` e `guardas.py`: dá para
montar e testar a seção sem arrastar google-cloud-logging/workflows.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

import requests

from src.config import GCP_PROJECT_ID
from src.reporting.formatting import AMBER, MUTED, RED, cell as _cell
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

GCP_REGION = "us-east1"

# Jobs dbt cobertos. Espelha a tabela de alvos do detector
# (`analytics-engineering/scripts/checa_deriva.sh`).
JOBS_DBT = ("dbt-futebol", "dbt-nba")

REPO_DETECTOR = "tech-lamjav/analytics-engineering"
WORKFLOW_DETECTOR = "deriva-imagem.yml"

# O detector é horário. Passou disso sem rodar, ele próprio é o problema — e o e-mail vira o
# único lugar onde isso aparece. O agendamento do GitHub Actions em repo de baixa atividade é
# best-effort e atrasa rotineiramente por horas (visto 2-7h entre execuções, não minutos) —
# 8h absorve essa variação sem descaracterizar o alarme. Ver falso positivo de 2026-08-31.
IDADE_MAXIMA_DETECTOR = timedelta(hours=8)

TIMEOUT = 15


@dataclass
class CarimboJob:
    """Carimbo de procedência de um Cloud Run Job.

    `error` preenchido = leitura degradada: a linha ainda aparece, dizendo por quê.
    `carimbo` None sem `error` = o job existe e NÃO tem carimbo — o mesmo estado que o
    detector trata como deriva.
    """

    job: str
    carimbo: Optional[str] = None
    commit: Optional[str] = None
    atualizado_em: Optional[str] = None
    error: Optional[str] = None


@dataclass
class VereditoDetector:
    """Última conclusão do workflow detector no GitHub Actions."""

    conclusion: Optional[str] = None   # "success" | "failure" | "cancelled" | ...
    quando: Optional[datetime] = None  # UTC
    url: Optional[str] = None
    error: Optional[str] = None

    @property
    def vermelho(self) -> bool:
        """Deriva confirmada pelo detector."""
        return self.conclusion is not None and self.conclusion != "success"

    @property
    def parado(self) -> bool:
        """O detector em si parou de rodar — o vigia caiu."""
        if self.error is not None or self.quando is None:
            return False
        return (datetime.now(timezone.utc) - self.quando) > IDADE_MAXIMA_DETECTOR


@dataclass
class ProcedenciaInfo:
    carimbos: list = field(default_factory=list)
    veredito: VereditoDetector = field(default_factory=VereditoDetector)

    @property
    def alarme(self) -> bool:
        """Vira o token [DERIVA] no assunto.

        Só dispara com veredito vermelho de verdade, ou com o detector parado. Erro
        transitório de leitura (rede, 5xx) NÃO vira alarme: o detector horário continua
        sendo o alarme primário e está vivo; fazer o assunto piscar por falha de leitura
        deste e-mail treinaria todo mundo a ignorá-lo — que é como esta classe de bug
        sobreviveu em primeiro lugar. Falha de leitura aparece degradada no corpo.
        """
        return self.veredito.vermelho or self.veredito.parado


def token_gcp() -> str:
    """Token de ADC p/ a Cloud Run Admin API. Publico porque `suite_dbt.py` chama a mesma
    API (executions) e nao ha razao p/ duas copias do mesmo handshake."""
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def collect_carimbos(jobs=JOBS_DBT) -> list:
    """Lê o carimbo de cada job. Nunca levanta: falha vira `error` na linha."""
    saida = []
    # Sem projeto a URL vira `projects/None` e a API responde 403 — um erro que se lê como
    # problema de permissão e manda o operador caçar IAM. O serviço recebe GCP_PROJECT_ID
    # por env var no deploy; localmente, sem `.env`, ele é None. Dizer isso explicitamente.
    if not GCP_PROJECT_ID:
        motivo = "GCP_PROJECT_ID nao configurado (env var ausente)"
        logger.warning(f"Secao de procedencia degradada: {motivo}")
        return [CarimboJob(job=j, error=motivo) for j in jobs]

    try:
        token = token_gcp()
    except Exception as e:
        logger.warning(f"Sem credencial p/ Cloud Run, secao de procedencia degradada: {e}")
        return [CarimboJob(job=j, error=f"{type(e).__name__}: {e}") for j in jobs]

    for job in jobs:
        url = (
            f"https://run.googleapis.com/v2/projects/{GCP_PROJECT_ID}"
            f"/locations/{GCP_REGION}/jobs/{job}"
        )
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
            resp.raise_for_status()
            doc = resp.json()
            container = doc["template"]["template"]["containers"][0]
            # `env` some do JSON quando o job nao tem nenhuma variavel — que era o estado dos
            # dois jobs antes deste trabalho. Ausencia da chave e ausencia de carimbo.
            env = {v.get("name"): v.get("value") for v in (container.get("env") or [])}
            saida.append(
                CarimboJob(
                    job=job,
                    carimbo=env.get("PROCEDENCIA_HASH"),
                    commit=env.get("PROCEDENCIA_SHA"),
                    atualizado_em=doc.get("updateTime"),
                )
            )
        except Exception as e:
            logger.warning(f"Nao consegui ler o carimbo de {job}: {e}")
            saida.append(CarimboJob(job=job, error=f"{type(e).__name__}: {e}"))
    return saida


def collect_veredito() -> VereditoDetector:
    """Lê a última conclusão do detector. Nunca levanta."""
    url = (
        f"https://api.github.com/repos/{REPO_DETECTOR}"
        f"/actions/workflows/{WORKFLOW_DETECTOR}/runs?per_page=1&status=completed"
    )
    try:
        resp = requests.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=TIMEOUT)
        # 404 = o workflow detector nao existe no master. Ambiguo por natureza: ou ainda nao
        # foi mergeado, ou alguem o apagou. Vira estado nomeado e NAO alarme, pela mesma
        # regra do erro de leitura — o proprio detector horario e o alarme primario, e um
        # assunto que pisca antes de o detector existir nasceria sendo ignorado.
        if resp.status_code == 404:
            return VereditoDetector(
                error=f"workflow {WORKFLOW_DETECTOR} nao encontrado em {REPO_DETECTOR} "
                      "(ainda nao mergeado, ou removido)"
            )
        resp.raise_for_status()
        runs = resp.json().get("workflow_runs") or []
        if not runs:
            return VereditoDetector(error="o detector ainda nao produziu nenhuma execucao concluida")
        run = runs[0]
        quando = None
        if run.get("updated_at"):
            quando = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
        return VereditoDetector(
            conclusion=run.get("conclusion"),
            quando=quando,
            url=run.get("html_url"),
        )
    except Exception as e:
        logger.warning(f"Veredito do detector de deriva indisponivel: {e}")
        return VereditoDetector(error=f"{type(e).__name__}: {e}")


def collect_procedencia() -> ProcedenciaInfo:
    """Duas leituras, ambas tolerantes a falha."""
    return ProcedenciaInfo(carimbos=collect_carimbos(), veredito=collect_veredito())


def _fmt_iso_curto(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    return iso.replace("T", " ")[:16] + " UTC"


def build_procedencia_section(info: Optional[ProcedenciaInfo]) -> str:
    """Seção de procedência. `None` omite a seção (mantém o e-mail de antes intacto)."""
    if info is None:
        return ""

    v = info.veredito
    if v.vermelho:
        cor, titulo = RED, "Deriva de imagem dbt — producao NAO corresponde ao master"
        veredito_txt = (
            "O detector horario acusou deriva. Os modelos dbt estao rodando de uma imagem "
            "que nao corresponde ao codigo no master, e a fase de guardas NAO vai acusar "
            "isso (ela roda da mesma imagem). Rode <code>./build-and-push.sh &lt;projeto&gt;</code> "
            "no repo analytics-engineering."
        )
    elif v.parado:
        cor, titulo = AMBER, "Detector de deriva parado"
        veredito_txt = (
            f"A ultima execucao do detector foi em {_fmt_iso_curto(v.quando.isoformat() if v.quando else None)}, "
            f"acima da folga de {int(IDADE_MAXIMA_DETECTOR.total_seconds() // 3600)}h. Workflow agendado em repo "
            "publico e desativado apos 60 dias sem atividade — conferir isso primeiro."
        )
    elif v.error:
        cor, titulo = MUTED, "Procedencia das imagens dbt"
        veredito_txt = f"Veredito do detector indisponivel: {escape(v.error)}. Carimbos abaixo mesmo assim."
    else:
        cor, titulo = MUTED, "Procedencia das imagens dbt"
        veredito_txt = "Detector horario verde: o que roda em producao corresponde ao master."

    if v.url:
        veredito_txt += f' <a href="{escape(v.url)}">ver execucao</a>'

    linhas = []
    for c in info.carimbos:
        if c.error:
            valor, cor_valor = f"leitura falhou: {escape(c.error)}", AMBER
        elif not c.carimbo:
            valor, cor_valor = "SEM CARIMBO", RED
        else:
            valor, cor_valor = f"<code>{escape(c.carimbo[:12])}</code>", None
        linhas.append(
            "<tr>"
            + _cell(escape(c.job))
            + _cell(valor, "left", cor_valor)
            + _cell(escape(c.commit or "—"))
            + _cell(_fmt_iso_curto(c.atualizado_em), "right")
            + "</tr>"
        )

    cabecalho = "".join(
        f'<th style="padding:6px 10px;border:1px solid #ddd;background:#f6f8fa;text-align:{al}">{rotulo}</th>'
        for rotulo, al in [
            ("Job", "left"), ("Carimbo", "left"), ("Commit", "left"), ("Deployado em", "right"),
        ]
    )

    return (
        f'<h3 style="margin:18px 0 6px;color:{cor}">{titulo}</h3>'
        f'<p style="margin:0 0 8px;color:{MUTED};font-size:13px">{veredito_txt}</p>'
        '<table style="border-collapse:collapse;font-size:13px">'
        f"<thead><tr>{cabecalho}</tr></thead><tbody>{''.join(linhas)}</tbody></table>"
    )
