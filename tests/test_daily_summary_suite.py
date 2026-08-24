"""DE#19: o resultado da suite dbt (fase 5) tem que chegar ao resumo diario.

A fase 5 roda os 291 data tests que ficam FORA de `tag:guarda` e `tag:taskf` (mais os 14
unit tests: 305 nos), que ate agora so rodavam no laptop de quem lembrasse.

O caso discriminante NAO e o mesmo das guardas. Guarda vermelha e `severity: error`: o
job cai, o conector levanta, o status conta a historia. Aqui tudo que acende hoje e
`severity: warn`, e `dbt test` sai com 0 quando so ha WARN — entao um resumo que lesse
so `suite_status` ficaria verde para sempre. O sinal e a linha do log:

    Done. PASS=295 WARN=10 ERROR=0 SKIP=0 NO-OP=0 TOTAL=305

(linha real da execucao `dbt-futebol-tqjk8`, dbt 1.11.2, 2026-08-21.)

E achar QUAL execucao do job `dbt-futebol` e a da fase 5 tambem nao e obvio: o job e o
mesmo nas quatro fases, e o nome que o conector devolve ao workflow e o da OPERACAO, nao
o da execucao. Quem separa e `encontra_execucao`, pelos ARGS que a Cloud Run Admin API
guarda em cada execucao.

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
    e_fase5,
    encontra_execucao,
    parse_done,
)

DIA = date(2026, 8, 21)
INICIO = datetime(2026, 8, 21, 3, 0, tzinfo=timezone.utc)
FIM = datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc)
QUANDO = datetime(2026, 8, 21, 13, 34, tzinfo=timezone.utc)

EXEC_CURTO = "dbt-futebol-tqjk8"
EXEC_LONGO = (
    "projects/smartbetting-dados/locations/us-east1/jobs/dbt-futebol/executions/" + EXEC_CURTO
)

# Args reais das quatro fases, como a Cloud Run Admin API v2 os devolve (lidos de
# execucoes de producao em 2026-08-21).
ARGS_FASE5 = ["dbt", "test", "--exclude", "tag:guarda", "tag:taskf",
              "--project-dir", "/app/dbt_futebol", "--profiles-dir", "/app/.dbt",
              "--target", "prod"]
ARGS_GUARDAS = ["dbt", "test", "--select", "tag:guarda",
                "--project-dir", "/app/dbt_futebol", "--profiles-dir", "/app/.dbt",
                "--target", "prod"]
ARGS_SNAPSHOT = ["dbt", "snapshot", "--select", "fact_value_opportunities_hist",
                 "--project-dir", "/app/dbt_futebol", "--target", "prod"]

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
# e_fase5 / encontra_execucao
# ---------------------------------------------------------------------------
def test_separa_a_fase5_das_outras_fases_do_MESMO_job():
    """O job `dbt-futebol` e o mesmo nas quatro fases — sem este predicado a secao poderia
    reportar a contagem das guardas (36 testes) como se fosse a da suite (305)."""
    assert e_fase5(ARGS_FASE5) is True
    assert e_fase5(ARGS_GUARDAS) is False
    assert e_fase5(ARGS_SNAPSHOT) is False
    assert e_fase5(["dbt", "run", "--select", "+fact_fixtures"]) is False
    assert e_fase5(None) is False


class _Resp:
    def __init__(self, doc):
        self._doc = doc

    def raise_for_status(self):
        pass

    def json(self):
        return self._doc


class _FakeRun:
    """Cloud Run Admin API v2: devolve as paginas na ordem, da mais recente p/ a antiga."""

    def __init__(self, *paginas):
        self._paginas = list(paginas)
        self.params = []

    def get(self, url, **kw):
        self.params.append(kw.get("params") or {})
        i = min(len(self.params) - 1, len(self._paginas) - 1)
        return _Resp(self._paginas[i])


def _execucao(nome, create_time, args):
    return {"name": f"projects/p/locations/l/jobs/dbt-futebol/executions/{nome}",
            "createTime": create_time,
            "template": {"containers": [{"args": args}]}}


@pytest.fixture(autouse=True)
def _sem_adc(monkeypatch):
    """`encontra_execucao` pede um token de ADC; nenhum teste deve tocar a rede."""
    monkeypatch.setattr("src.reporting.suite_dbt.token_gcp", lambda: "token-de-teste")


def test_encontra_a_execucao_da_fase5_ignorando_as_outras():
    sessao = _FakeRun({"executions": [
        _execucao("dbt-futebol-zzz", "2026-08-21T18:00:00.123456789Z", ARGS_GUARDAS),
        _execucao(EXEC_CURTO, "2026-08-21T13:31:56.078666Z", ARGS_FASE5),
        _execucao("dbt-futebol-aaa", "2026-08-21T12:13:30.367280Z", ARGS_SNAPSHOT),
    ]})

    nome, quando = encontra_execucao(INICIO, FIM, sessao=sessao)

    assert nome == EXEC_CURTO
    assert quando.hour == 13


def test_para_de_paginar_ao_sair_da_janela():
    """A API devolve da mais recente p/ a mais antiga: passou do inicio do dia, acabou.

    Sem a parada, um dia sem fase 5 varreria as 5 paginas (1.000 execucoes) a toa.
    """
    sessao = _FakeRun({"executions": [
        _execucao("velha", "2026-08-19T13:00:00Z", ARGS_FASE5),
    ], "nextPageToken": "tem-mais"})

    nome, _ = encontra_execucao(INICIO, FIM, sessao=sessao)

    assert nome is None
    assert len(sessao.params) == 1, "nao devia ter pedido a proxima pagina"


def test_ignora_execucao_posterior_a_janela():
    """O resumo roda p/ o dia ANTERIOR: a execucao de hoje nao pode entrar no e-mail de
    ontem."""
    sessao = _FakeRun({"executions": [
        _execucao("de-hoje", "2026-08-22T13:00:00Z", ARGS_FASE5),
        _execucao(EXEC_CURTO, "2026-08-21T13:31:56Z", ARGS_FASE5),
    ]})

    nome, _ = encontra_execucao(INICIO, FIM, sessao=sessao)

    assert nome == EXEC_CURTO


# ---------------------------------------------------------------------------
# collect_suite
# ---------------------------------------------------------------------------
class _Entry:
    def __init__(self, payload):
        self.payload = payload


class _FakeLogging:
    def __init__(self, entries):
        self._entries = entries
        self.kwargs = []

    def list_entries(self, **kwargs):
        self.kwargs.append(kwargs)
        return iter(self._entries)


def _sessao_ok():
    return _FakeRun({"executions": [
        _execucao(EXEC_CURTO, "2026-08-21T13:31:56.078666Z", ARGS_FASE5),
    ]})


def _run(**extra):
    base = {"quando": QUANDO, "status": "SUCCESS"}
    base.update(extra)
    return SuiteRun(**base)


def test_sem_execucao_no_dia_a_secao_some():
    """Workflow antigo, sem o campo no log_completion: melhor sumir do que mentir."""
    assert collect_suite([], INICIO, FIM) is None
    assert build_suite_section(None) == ""


def test_acha_a_contagem_pela_execucao():
    cliente = _FakeLogging([_Entry(LINHA_REAL)])

    info = collect_suite([_run()], INICIO, FIM, client=cliente, sessao=_sessao_ok())

    assert info.contagem.warn == 10
    assert info.erro_leitura is None
    assert info.execucao == EXEC_CURTO
    assert EXEC_CURTO in cliente.kwargs[0]["filter_"]


def test_le_a_ULTIMA_linha_done_da_execucao():
    """`maxRetries=1` no job: uma execucao que falha e repete tem DUAS linhas `Done.`.

    Ascendente (o default do SDK) reportaria a tentativa que FALHOU de uma execucao que
    terminou verde — `[SUITE]` falso, que e o alarme que treina o time a ignorar o e-mail.
    """
    cliente = _FakeLogging([_Entry(LINHA_REAL)])

    collect_suite([_run()], INICIO, FIM, client=cliente, sessao=_sessao_ok())

    assert cliente.kwargs[0].get("order_by") == "timestamp desc"


def test_filtro_do_logging_e_limitado_no_tempo():
    """O resumo roda ~00:05 BRT p/ o dia anterior: sem limite, a consulta varre os 30 dias
    de retencao inteiros para achar uma linha."""
    cliente = _FakeLogging([_Entry(LINHA_REAL)])

    collect_suite([_run()], INICIO, FIM, client=cliente, sessao=_sessao_ok())

    assert "timestamp>=" in cliente.kwargs[0]["filter_"]
    assert "timestamp<" in cliente.kwargs[0]["filter_"]


def test_logging_indisponivel_nao_derruba_o_resumo():
    """A secao degrada, como em procedencia.py — o e-mail do dia nao pode deixar de sair."""
    cliente = MagicMock()
    cliente.list_entries.side_effect = RuntimeError("permissao negada")

    info = collect_suite([_run()], INICIO, FIM, client=cliente, sessao=_sessao_ok())

    assert info.erro_leitura is not None
    assert info.contagem is None
    assert "Suite dbt" in build_suite_section(info)


def test_cloud_run_indisponivel_nao_derruba_o_resumo():
    sessao = MagicMock()
    sessao.get.side_effect = RuntimeError("403")

    info = collect_suite([_run()], INICIO, FIM, client=_FakeLogging([]), sessao=sessao)

    assert info.erro_leitura is not None
    assert "Suite dbt" in build_suite_section(info)


def test_execucao_sem_linha_done_vira_leitura_degradada():
    info = collect_suite([_run()], INICIO, FIM, client=_FakeLogging([]), sessao=_sessao_ok())

    assert info.contagem is None
    assert "Done. PASS=" in info.erro_leitura


def test_nao_procura_execucao_quando_a_fase_nao_rodou():
    """`NOT_RUN` e resposta completa: nao ha execucao para achar, e pedir a lista seria
    uma chamada de rede para descobrir isso."""
    sessao = _sessao_ok()

    info = collect_suite([_run(status="NOT_RUN")], INICIO, FIM, sessao=sessao)

    assert info.execucao is None
    assert sessao.params == []


def test_usa_a_passagem_MAIS_RECENTE_do_dia():
    """O workflow futebol pode rodar mais de uma vez (backfill manual)."""
    antiga = _run(quando=datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc), status="NOT_RUN")
    info = collect_suite([antiga, _run()], INICIO, FIM,
                         client=_FakeLogging([_Entry(LINHA_REAL)]), sessao=_sessao_ok())

    assert info.run.status == "SUCCESS"
    assert info.execucoes_no_dia == 2


def test_execucao_sem_timestamp_nao_levanta():
    """Defensivo: ordenar None com None levantaria TypeError e mataria o resumo inteiro."""
    info = collect_suite([_run(quando=None), _run(quando=None)], INICIO, FIM,
                         client=_FakeLogging([]), sessao=_sessao_ok())

    assert info is not None


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


def test_job_caido_sem_contagem_alarma():
    """O `except` da fase 5 pega ERROR>=1 mas tambem OOM, timeout e falha de imagem."""
    info = SuiteInfo(run=_run(status="PARTIAL_FAILURE"))

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
    """Gate a montante desviou. "Nao rodou" reportado como verde e exatamente o buraco que
    o C4 fechou nas guardas."""
    html = build_suite_section(SuiteInfo(run=_run(status="NOT_RUN")))

    assert "NAO rodou" in html


def test_secao_vermelha_diz_onde_olhar():
    html = build_suite_section(
        SuiteInfo(run=_run(), contagem=ContagemDbt(290, 10, 5, 0, 305), execucao=EXEC_CURTO)
    )

    assert "dbt-futebol" in html
    assert EXEC_CURTO in html


def test_job_caido_SEM_contagem_renderiza_VERMELHO():
    """O caso que a fase existe para pegar, e o unico que estava saindo errado.

    Quando o job cai e nao sobra linha `Done.` legivel, `contagem` e None. Se o ramo
    vermelho exigisse contagem, o dia caia no ramo cinza de "leitura degradada": assunto
    com [SUITE] e corpo dizendo que nao ha nada para ver. Vermelho sem contagem ainda diz
    onde olhar, que e a unica acao possivel.
    """
    info = SuiteInfo(run=_run(status="PARTIAL_FAILURE"), execucao=EXEC_CURTO,
                     erro_leitura="execucao nao tem linha 'Done. PASS='")

    html = build_suite_section(info)

    assert "#cf222e" in html, "cor vermelha"
    assert "VERMELHA" in html
    assert EXEC_CURTO in html


def test_leitura_degradada_com_a_fase_VERDE_nao_finge_vermelho():
    """Contraparte: falha de leitura nao e falha de dado. Cinza, com o motivo."""
    info = SuiteInfo(run=_run(), erro_leitura="RuntimeError: 403")

    html = build_suite_section(info)

    assert "#cf222e" not in html
    assert "403" in html


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


def test_collect_from_logging_captura_a_passagem_pela_fase5(daily_summary):
    agg = _agrega(daily_summary, _payload())

    runs = agg["workflow-futebol"].suite_runs
    assert len(runs) == 1
    assert runs[0].status == "SUCCESS"
    assert runs[0].quando == QUANDO


def test_workflow_sem_a_fase5_nao_vira_falso_positivo(daily_summary):
    """A maioria dos workflows nao roda a suite e nao emite o campo. Ausencia != vermelho,
    e tambem != "nao rodou" — a secao nem existe para eles."""
    sem_campo = _payload(workflow_name="workflow_futebol_sync")
    sem_campo.pop("suite_status")
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

    _, html = daily_summary.build_html(DIA, agg, None, None, None)

    assert "Suite dbt" not in html
