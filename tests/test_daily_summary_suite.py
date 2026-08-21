"""DE#19: o resultado da suite dbt (fase 5) tem que chegar ao resumo diario.

A fase 5 roda os ~291 testes que ficam FORA de `tag:guarda` — os `relationships`,
`not_null` e `unique` dos marts, que ate agora so rodavam no laptop de quem lembrasse.

O caso discriminante NAO e o mesmo das guardas. Guarda vermelha e `severity: error`: o
job cai, o conector levanta, o status conta a historia. Aqui tudo que acende hoje e
`severity: warn`, e `dbt test` sai com 0 quando so ha WARN — entao um resumo que lesse
so `suite_status` ficaria verde para sempre. O sinal e a linha do log:

    Done. PASS=295 WARN=10 ERROR=0 SKIP=0 NO-OP=0 TOTAL=305

(linha real da execucao `dbt-futebol-tqjk8`, dbt 1.11.2, 2026-08-21.)

`suite_dbt` importa o SDK de logging preguicosamente, dentro do coletor, entao a logica
e testada aqui sem stub nenhum. So a fiacao dentro de `daily_summary` precisa dos stubs.
"""
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.reporting.suite_dbt import (
    ContagemDbt,
    SuiteInfo,
    SuiteRun,
    build_suite_section,
    collect_suite,
    parse_done,
)

DIA = date(2026, 8, 21)
INICIO = datetime(2026, 8, 21, 3, 0, tzinfo=timezone.utc)
FIM = datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc)
QUANDO = datetime(2026, 8, 21, 13, 34, tzinfo=timezone.utc)

EXEC_LONGO = (
    "projects/smartbetting-dados/locations/us-east1/jobs/dbt-futebol/executions/dbt-futebol-tqjk8"
)
EXEC_CURTO = "dbt-futebol-tqjk8"

# Linha real, com o prefixo ANSI e o horario que o Cloud Run entrega no textPayload.
LINHA_REAL = "\x1b[0m13:34:08  Done. PASS=295 WARN=10 ERROR=0 SKIP=0 NO-OP=0 TOTAL=305"


# ---------------------------------------------------------------------------
# parse_done
# ---------------------------------------------------------------------------
def test_le_a_linha_real_do_dbt_1_11_com_no_op():
    """O campo `NO-OP` fica ENTRE `SKIP` e `TOTAL` no dbt 1.11.

    Um regex posicional que exigisse `SKIP=... TOTAL=...` adjacentes casa com a
    documentacao e NAO casa com o que producao emite — e o modo de falha e mudo: a secao
    diria "contagem indisponivel" todo dia sem ninguem entender por que.
    """
    c = parse_done(LINHA_REAL)

    assert c == ContagemDbt(passou=295, warn=10, error=0, skip=0, total=305)


def test_le_a_linha_sem_no_op():
    """Versao anterior do dbt (e a que a documentacao mostra). As duas tem que passar."""
    c = parse_done("13:34:08  Done. PASS=295 WARN=10 ERROR=0 SKIP=0 TOTAL=305")

    assert (c.passou, c.warn, c.error, c.total) == (295, 10, 0, 305)


def test_campo_novo_do_dbt_nao_quebra_a_leitura():
    """Contraparte do NO-OP: campo que ainda nao existe entra sem derrubar a linha."""
    c = parse_done("Done. PASS=1 WARN=0 ERROR=0 SKIP=0 NO-OP=0 QUALQUER-COISA=7 TOTAL=1")

    assert (c.passou, c.total) == (1, 1)


def test_linha_que_nao_e_fechamento_de_teste():
    assert parse_done("13:32:52  Concurrency: 4 threads (target='prod')") is None
    assert parse_done("") is None
    assert parse_done(None) is None


# ---------------------------------------------------------------------------
# collect_suite
# ---------------------------------------------------------------------------
class _Entry:
    def __init__(self, payload):
        self.payload = payload


class _FakeLogging:
    def __init__(self, entries):
        self._entries = entries
        self.filtros = []

    def list_entries(self, **kwargs):
        self.filtros.append(kwargs.get("filter_", ""))
        return iter(self._entries)


def _run(**extra):
    base = {"quando": QUANDO, "status": "SUCCESS", "execucao": EXEC_LONGO}
    base.update(extra)
    return SuiteRun(**base)


def test_sem_execucao_no_dia_a_secao_some():
    """Workflow antigo, sem o campo no log_completion: melhor sumir do que mentir."""
    assert collect_suite([]) is None
    assert build_suite_section(None) == ""


def test_acha_a_contagem_pela_execucao():
    cliente = _FakeLogging([_Entry(LINHA_REAL)])

    info = collect_suite([_run()], client=cliente)

    assert info.contagem.warn == 10
    assert info.erro_leitura is None
    # Filtra pela execucao, e o label do Cloud Logging usa o nome CURTO — o workflow emite
    # o nome do recurso inteiro, entao ha uma conversao no meio que precisa estar certa.
    assert EXEC_CURTO in cliente.filtros[0]
    assert "projects/" not in cliente.filtros[0].split("execution_name")[1]


def test_logging_indisponivel_nao_derruba_o_resumo():
    """A secao degrada, como em procedencia.py — o e-mail do dia nao pode deixar de sair."""
    cliente = MagicMock()
    cliente.list_entries.side_effect = RuntimeError("permissao negada")

    info = collect_suite([_run()], client=cliente)

    assert info.erro_leitura is not None
    assert info.contagem is None
    assert "Suite dbt" in build_suite_section(info)


def test_execucao_sem_linha_done_vira_leitura_degradada():
    info = collect_suite([_run()], client=_FakeLogging([]))

    assert info.contagem is None
    assert "Done. PASS=" in info.erro_leitura


def test_usa_a_execucao_MAIS_RECENTE_do_dia():
    """O workflow futebol pode rodar mais de uma vez (backfill manual). O estado atual do
    mart e o da ultima."""
    antiga = _run(quando=datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc), execucao="x/velha")
    info = collect_suite([antiga, _run()], client=_FakeLogging([_Entry(LINHA_REAL)]))

    assert info.run.execucao_curta == EXEC_CURTO
    assert info.execucoes_no_dia == 2


def test_execucao_sem_timestamp_nao_levanta():
    """Defensivo: ordenar None com None levantaria TypeError e mataria o resumo inteiro."""
    info = collect_suite([_run(quando=None), _run(quando=None)], client=_FakeLogging([]))

    assert info is not None


def test_fase_vermelha_sem_nome_de_execucao():
    """Quando o job cai, o conector levanta antes de o workflow nomear a execucao. Sobra o
    status — e ele basta, porque `dbt test` so cai com ERROR>=1."""
    info = collect_suite([_run(status="PARTIAL_FAILURE", execucao="")], client=_FakeLogging([]))

    assert info.alarme is True
    assert info.contagem is None


# ---------------------------------------------------------------------------
# alarme
# ---------------------------------------------------------------------------
def test_warn_nao_alarma():
    """WARN>0 e o estado PERMANENTE da suite (orfaos de injuries/standings + baseline de
    cobertura). Assunto que pisca todo dia treina o time a ignorar o e-mail — foi assim
    que esta classe de bug sobreviveu."""
    info = SuiteInfo(run=_run(), contagem=ContagemDbt(295, 10, 0, 0, 305))

    assert info.alarme is False


def test_error_alarma():
    info = SuiteInfo(run=_run(), contagem=ContagemDbt(290, 10, 5, 0, 305))

    assert info.alarme is True


# ---------------------------------------------------------------------------
# build_suite_section
# ---------------------------------------------------------------------------
def test_secao_aparece_MESMO_verde_com_o_numero_de_warn():
    """A diferenca de contrato com `guardas.py`, e a razao de existir da fase.

    Guarda so aparece quando acende, porque acender e anormal. Aqui WARN>0 e o normal: o
    valor esta em COMPARAR o numero com o de ontem. Uma secao que sumisse no verde nao
    deixaria ninguem perceber WARN indo de 10 para 40.
    """
    html = build_suite_section(SuiteInfo(run=_run(), contagem=ContagemDbt(295, 10, 0, 0, 305)))

    assert "295" in html and "10" in html and "305" in html


def test_secao_diz_quando_a_fase_nao_rodou():
    """Gate a montante desviou (extract-fixtures ou o rebuild falhou). "Nao rodou"
    reportado como verde e exatamente o buraco que o C4 fechou nas guardas."""
    html = build_suite_section(SuiteInfo(run=_run(status="NOT_RUN", execucao="")))

    assert "NAO rodou" in html


def test_secao_vermelha_diz_onde_olhar():
    html = build_suite_section(SuiteInfo(run=_run(), contagem=ContagemDbt(290, 10, 5, 0, 305)))

    assert "dbt-futebol" in html
    assert EXEC_CURTO in html


# ---------------------------------------------------------------------------
# fiacao dentro do resumo diario
# ---------------------------------------------------------------------------
_STUBS_GCP = (
    "google.cloud.logging",
    "google.cloud.workflows",
    "google.cloud.workflows.executions_v1",
    "google.cloud.workflows.executions_v1.types",
)


@pytest.fixture
def daily_summary():
    """Mesma fiacao de stubs do test_daily_summary_wiring."""
    alvos = (*_STUBS_GCP, "src.reporting.daily_summary")
    salvos = {nome: sys.modules.get(nome) for nome in alvos}
    for nome in _STUBS_GCP:
        sys.modules[nome] = MagicMock()
    sys.modules.pop("src.reporting.daily_summary", None)
    try:
        import src.reporting.daily_summary as mod

        yield mod
    finally:
        for nome, antigo in salvos.items():
            if antigo is None:
                sys.modules.pop(nome, None)
            else:
                sys.modules[nome] = antigo


class _EntryPayload:
    def __init__(self, payload, timestamp=QUANDO):
        self.payload = payload
        self.timestamp = timestamp


def _payload(**extra):
    """log_completion do workflow_futebol, campo a campo como o YAML emite."""
    base = {
        "message": "Workflow execution completed",
        "workflow_name": "workflow_futebol",
        "status": "SUCCESS",
        "guardas_status": "SUCCESS",
        "suite_status": "SUCCESS",
        "suite_execution": EXEC_LONGO,
        "mode": "current",
        "duration_seconds": 900.0,
    }
    base.update(extra)
    return base


def _agrega(daily_summary, *payloads):
    agg = defaultdict(daily_summary.WFAgg)
    daily_summary.collect_from_logging(
        MagicMock(list_entries=lambda **kw: iter([_EntryPayload(p) for p in payloads])),
        INICIO,
        FIM,
        agg,
    )
    return agg


def test_collect_from_logging_captura_a_execucao_da_fase5(daily_summary):
    agg = _agrega(daily_summary, _payload())

    runs = agg["workflow-futebol"].suite_runs
    assert len(runs) == 1
    assert runs[0].execucao_curta == EXEC_CURTO
    assert runs[0].status == "SUCCESS"


def test_workflow_sem_a_fase5_nao_vira_falso_positivo(daily_summary):
    """A maioria dos workflows nao roda a suite e nao emite o campo. Ausencia != vermelho,
    e tambem != "nao rodou" — a secao nem existe para eles."""
    sem_campo = _payload(workflow_name="workflow_futebol_sync")
    sem_campo.pop("suite_status")
    sem_campo.pop("suite_execution")
    agg = _agrega(daily_summary, sem_campo)

    assert agg["workflow-futebol-sync"].suite_runs == []


def test_build_html_concatena_a_secao_da_suite(daily_summary):
    agg = _agrega(daily_summary, _payload())
    info = SuiteInfo(run=_run(), contagem=ContagemDbt(295, 10, 0, 0, 305))

    assunto, html = daily_summary.build_html(DIA, agg, None, None, info)

    assert "Suite dbt" in html
    assert "295" in html
    assert assunto.startswith("[OK]"), "WARN nao pode acender o assunto"


def test_assunto_denuncia_ERROR_na_suite_com_o_workflow_verde(daily_summary):
    """Mesmo buraco do C4, um andar acima: a fase nao derruba workflow nenhum, entao sem
    token no assunto ela e alarme mudo."""
    agg = _agrega(daily_summary, _payload())
    info = SuiteInfo(run=_run(), contagem=ContagemDbt(290, 10, 5, 0, 305))

    assunto, _ = daily_summary.build_html(DIA, agg, None, None, info)

    assert not assunto.startswith("[OK]")
    assert "[SUITE]" in assunto


def test_assunto_soma_os_tokens_sem_fundir(daily_summary):
    """Tokens JUSTAPOSTOS: filtro de caixa de entrada casa por substring, e um assunto
    fundido nao casaria com nenhuma das regras — justamente no pior dia."""
    agg = _agrega(daily_summary, _payload(status="PARTIAL_FAILURE", guardas_status="PARTIAL_FAILURE"))
    info = SuiteInfo(run=_run(), contagem=ContagemDbt(290, 10, 5, 0, 305))

    assunto, _ = daily_summary.build_html(DIA, agg, None, None, info)

    assert "[FALHAS]" in assunto
    assert "[GUARDA]" in assunto
    assert "[SUITE]" in assunto


def test_email_de_antes_fica_intacto_sem_a_secao(daily_summary):
    """`suite=None` (workflow ainda nao deployado) nao pode mudar o e-mail existente."""
    agg = _agrega(daily_summary, _payload())

    _, com = daily_summary.build_html(DIA, agg, None, None, None)

    assert "Suite dbt" not in com
