"""Seção de procedência das imagens dbt no e-mail do resumo diário.

Os modelos dbt rodam de uma imagem Docker pré-buildada, não do master. Quando alguém
mergeia e esquece de rebuildar, produção segue rodando código velho — e a fase de guardas
dbt **não acusa**, porque ela roda da MESMA imagem derivada. Foi assim que o fix do de-vig
ficou 2 dias fora de produção (perdeu o build por 70 segundos) e o `dbt_nba` ficou ~6
semanas atrás do master. Ver `analytics-engineering/docs/adr/0001`.

O alarme primário são os detectores horários no GitHub Actions — dois, um por frota, cada
um FORA da imagem/serviço que vigia. Esta seção é o segundo canal: o e-mail diário.

DUAS LEITURAS, E POR QUÊ:

1. **Carimbo de cada job dbt**, pela Cloud Run Admin API v2. Dá o valor concreto que um
   operador precisa para agir (qual hash está em produção, de qual commit, desde quando).
   Só os jobs — os 29 serviços não têm tabela de carimbo aqui (ver DE #56).
2. **Veredito de CADA detector**, pela API pública do GitHub Actions — DUAS frotas desde a
   DE #56: as imagens dbt (`analytics-engineering/deriva-imagem.yml`) e os serviços Cloud
   Run (`data-engineering/deriva-servicos.yml`, este repo). O carimbo sozinho não permite
   JULGAR daqui: o hash esperado é calculado da árvore de cada repo, que o outro não tem.
   Quem já fez essa conta é o detector de cada frota — então lemos a conclusão dele em vez
   de refazer o cálculo. Repos públicos, GET sem token.

   ⚠️ MESMO TOKEN `[DERIVA]` PARA AS DUAS FROTAS, não um token novo por frota: a ação do
   operador é da mesma classe — "produção não corresponde ao master" —, e token novo seria
   vocabulário novo para o mesmo conceito. O corpo do e-mail diz QUAL frota derivou; o
   assunto só precisa dizer QUE alguma derivou.

A leitura 2 também vigia os vigias: se um detector parou de rodar (workflow agendado em
repo público é desativado após 60 dias sem atividade), o silêncio dele vira notícia aqui —
por frota, então um detector parado não esconde o outro.

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

# AS DUAS FROTAS (DE #56): (nome p/ exibição, repo do detector, arquivo do workflow,
# remédio a mostrar quando vermelho). Acrescentar uma frota nova é UMA linha aqui — o
# resto do módulo já generaliza (`collect_vereditos` itera esta tupla, `alarme` é o OR).
FROTAS = (
    (
        "imagens dbt",
        "tech-lamjav/analytics-engineering",
        "deriva-imagem.yml",
        "Rode <code>./build-and-push.sh &lt;projeto&gt;</code> no repo analytics-engineering.",
    ),
    (
        "serviços Cloud Run",
        "tech-lamjav/data-engineering",
        "deriva-servicos.yml",
        "Rode <code>./scripts/deploy_cloud_run.sh &lt;servico&gt;</code> neste repo.",
    ),
)

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
    """Última conclusão do workflow detector de UMA frota no GitHub Actions."""

    frota: str = ""                    # "imagens dbt" | "serviços Cloud Run" | ...
    repo: str = ""
    remedio: str = ""                  # HTML pronto p/ o corpo, quando vermelho
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
    # UMA entrada por frota (DE #56) — antes desta entrega era um `VereditoDetector`
    # singular, só das imagens dbt. `alarme` é o OR: qualquer frota vermelha ou parada
    # sobe o MESMO token `[DERIVA]`, sem distinguir qual no assunto — é o corpo que diz.
    vereditos: list = field(default_factory=list)

    @property
    def alarme(self) -> bool:
        """Vira o token [DERIVA] no assunto.

        Só dispara com veredito vermelho de verdade, ou com o detector parado, em
        QUALQUER frota. Erro transitório de leitura (rede, 5xx) NÃO vira alarme: o
        detector horário daquela frota continua sendo o alarme primário e está vivo;
        fazer o assunto piscar por falha de leitura deste e-mail treinaria todo mundo a
        ignorá-lo — que é como esta classe de bug sobreviveu em primeiro lugar. Falha de
        leitura aparece degradada no corpo.
        """
        return any(v.vermelho or v.parado for v in self.vereditos)


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


def collect_veredito(frota: str, repo: str, workflow: str, remedio: str = "") -> VereditoDetector:
    """Lê a última conclusão do detector de UMA frota. Nunca levanta."""
    url = (
        f"https://api.github.com/repos/{repo}"
        f"/actions/workflows/{workflow}/runs?per_page=1&status=completed"
    )
    try:
        resp = requests.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=TIMEOUT)
        # 404 = o workflow detector nao existe no master. Ambiguo por natureza: ou ainda nao
        # foi mergeado, ou alguem o apagou. Vira estado nomeado e NAO alarme, pela mesma
        # regra do erro de leitura — o proprio detector horario e o alarme primario, e um
        # assunto que pisca antes de o detector existir nasceria sendo ignorado.
        if resp.status_code == 404:
            return VereditoDetector(
                frota=frota, repo=repo, remedio=remedio,
                error=f"workflow {workflow} nao encontrado em {repo} "
                      "(ainda nao mergeado, ou removido)"
            )
        resp.raise_for_status()
        runs = resp.json().get("workflow_runs") or []
        if not runs:
            return VereditoDetector(
                frota=frota, repo=repo, remedio=remedio,
                error="o detector ainda nao produziu nenhuma execucao concluida",
            )
        run = runs[0]
        quando = None
        if run.get("updated_at"):
            quando = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
        return VereditoDetector(
            frota=frota, repo=repo, remedio=remedio,
            conclusion=run.get("conclusion"),
            quando=quando,
            url=run.get("html_url"),
        )
    except Exception as e:
        logger.warning(f"Veredito do detector de deriva ({frota}) indisponivel: {e}")
        return VereditoDetector(frota=frota, repo=repo, remedio=remedio, error=f"{type(e).__name__}: {e}")


def collect_vereditos(frotas=FROTAS) -> list:
    """Um veredito por frota (DE #56). Cada leitura é independente e tolerante a falha —
    uma frota indisponível não derruba a leitura da outra."""
    return [collect_veredito(*f) for f in frotas]


def collect_procedencia() -> ProcedenciaInfo:
    """Duas leituras, ambas tolerantes a falha."""
    return ProcedenciaInfo(carimbos=collect_carimbos(), vereditos=collect_vereditos())


def _fmt_iso_curto(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    return iso.replace("T", " ")[:16] + " UTC"


def build_procedencia_section(info: Optional[ProcedenciaInfo]) -> str:
    """Seção de procedência. `None` omite a seção (mantém o e-mail de antes intacto)."""
    if info is None:
        return ""

    # ⚠️ DE #56: um parágrafo POR FROTA, porque o corpo tem de dizer QUAL derivou — o
    # assunto (`alarme`, no daily_summary.py) já fundiu as duas no mesmo token. O título
    # e a cor do bloco inteiro seguem o PIOR estado entre as frotas (vermelho > parado >
    # erro > verde), na mesma ordem de severidade que cada `<p>` individual usa.
    paragrafos = []
    pior_cor, titulo = MUTED, "Procedência das imagens dbt e dos serviços Cloud Run"
    for v in info.vereditos:
        rotulo = escape(v.frota) or "detector"
        if v.vermelho:
            cor = RED
            texto = (
                f"<b>{rotulo}</b>: o detector horário acusou deriva — produção não "
                f"corresponde ao master, e a fase de guardas não acusa isso (ela roda "
                f"da mesma imagem/serviço). {v.remedio}"
            )
        elif v.parado:
            cor = AMBER
            texto = (
                f"<b>{rotulo}</b>: a última execução do detector foi em "
                f"{_fmt_iso_curto(v.quando.isoformat() if v.quando else None)}, acima da folga de "
                f"{int(IDADE_MAXIMA_DETECTOR.total_seconds() // 3600)}h. Workflow agendado em repo "
                "público é desativado após 60 dias sem atividade — conferir isso primeiro."
            )
        elif v.error:
            cor = MUTED
            texto = f"<b>{rotulo}</b>: veredito indisponível — {escape(v.error)}. Carimbos abaixo mesmo assim."
        else:
            cor = MUTED
            texto = f"<b>{rotulo}</b>: detector horário verde — o que roda em produção corresponde ao master."

        if v.url:
            texto += f' <a href="{escape(v.url)}">ver execução</a>'

        paragrafos.append(f'<p style="margin:0 0 6px;color:{MUTED};font-size:13px">{texto}</p>')

        # RED > AMBER > MUTED, nesta ordem — o bloco herda a cor da frota mais grave.
        if cor == RED:
            pior_cor, titulo = RED, "Deriva detectada — produção NÃO corresponde ao master"
        elif cor == AMBER and pior_cor != RED:
            pior_cor, titulo = AMBER, "Detector(es) de deriva parado(s)"

    cor = pior_cor
    veredito_txt = "".join(paragrafos)

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
        f"{veredito_txt}"
        '<table style="border-collapse:collapse;font-size:13px">'
        f"<thead><tr>{cabecalho}</tr></thead><tbody>{''.join(linhas)}</tbody></table>"
    )
