"""Fallback, falha de troca e aviso de sombra órfã no resumo diário (DE#108, histórias 9/20/27/54).

Duas camadas, como na seção do tamanho do DEV:
- a lógica (agregar o dia, renderizar a seção) em `src.reporting.troca_sync`, sem SDK nenhum;
- a fiação em `daily_summary` (leitura do log de conclusão do `workflow_futebol_sync`), com o SDK do
  Google stubado e o fake de Cloud Logging.

Regra herdada de `dev_size_mb`/`guardas_status`: chave AUSENTE ou nula no log de conclusão não é
vermelho nem leitura, é "esse log não carrega isso" (409, imagem velha, workflow sem a troca).
"""
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.reporting.troca_sync import (
    blocos_do_log,
    build_troca_section,
    collect_troca,
    nomes_das_falhas,
)

DIA = date(2026, 10, 1)
H = lambda h, m=0: datetime(2026, 10, 1, h, m, tzinfo=timezone.utc)  # noqa: E731

_STUBS_GCP = (
    "google.cloud.logging",
    "google.cloud.workflows",
    "google.cloud.workflows.executions_v1",
    "google.cloud.workflows.executions_v1.types",
)


def _resumo(trocas=(), fallback_tabelas=(), falhas=(), avisos=()):
    return {
        "summary": {
            "synced": len(trocas), "skipped": 0, "fallback": len(fallback_tabelas),
            "falhas_de_troca": len(falhas),
            "fallback_tabelas": list(fallback_tabelas), "trocas": list(trocas),
        },
        "falhas": list(falhas),
        "avisos": list(avisos),
    }


def _troca(table, ms, tentativas=1, modo="troca"):
    return {"table": table, "modo": modo, "troca_ms": ms, "tentativas": tentativas}


# ------------------------------------------------------------------
# Lógica pura
# ------------------------------------------------------------------
def test_sem_nada_a_relatar_nao_ha_estado_e_a_secao_e_omitida():
    """Lançamento escuro (troca desligada): o e-mail de antes fica intacto."""
    assert collect_troca([]) is None
    assert collect_troca([(H(10), "prd", _resumo())]) is None
    assert build_troca_section(None) == ""


def test_duracao_de_cada_troca_por_ambiente_e_tabela_media_e_maxima():
    info = collect_troca([
        (H(10), "prd", _resumo(trocas=[_troca("fact_fixtures", 10.0), _troca("fact_h2h", 4.0)])),
        (H(11), "prd", _resumo(trocas=[_troca("fact_fixtures", 30.0, tentativas=3)])),
    ])
    t = info.trocas[("prd", "fact_fixtures")]
    assert (t.cargas, t.media_ms, t.max_ms, t.max_tentativas) == (2, 20.0, 30.0, 3)
    assert info.trocas[("prd", "fact_h2h")].cargas == 1
    html = build_troca_section(info)
    assert "fact_fixtures" in html and "30.0" in html and "fact_h2h" in html


def test_fallback_conta_as_cargas_por_tabela_e_aparece_como_aviso_para_o_app():
    info = collect_troca([
        (H(10), "prd", _resumo(fallback_tabelas=["fact_fixtures"])),
        (H(11), "prd", _resumo(fallback_tabelas=["fact_fixtures"])),
    ])
    assert info.fallbacks == {("prd", "fact_fixtures"): 2}
    html = build_troca_section(info)
    assert "fallback" in html.lower() and "fact_fixtures" in html and "#9a6700" in html


def test_falha_de_troca_traz_o_nome_da_tabela_o_motivo_e_o_ambiente_em_vermelho():
    info = collect_troca([
        (H(12, 30), "prd", _resumo(falhas=[{"table": "fact_fixtures", "motivo": "lock_timeout"}])),
    ])
    assert [(f.env, f.table, f.motivo) for f in info.falhas] == [("prd", "fact_fixtures", "lock_timeout")]
    html = build_troca_section(info)
    assert "fact_fixtures" in html and "lock_timeout" in html and "#cf222e" in html


def test_aviso_de_sombra_orfa_aparece_uma_vez_mesmo_repetido():
    aviso = "sombra órfã futebol.fact_h2h__new com 3.1 h (> 2 h): execução anterior interrompida"
    info = collect_troca([
        (H(10), "prd", _resumo(avisos=[aviso])),
        (H(11), "prd", _resumo(avisos=[aviso])),
    ])
    assert len(info.avisos) == 1
    assert "fact_h2h__new" in build_troca_section(info)


def test_resumo_malformado_e_ignorado_sem_derrubar_a_coleta():
    lixo = [
        (H(9), "prd", "texto"),
        (H(9), "prd", {"summary": "x", "falhas": "y", "avisos": 3}),
        (H(9), "prd", {"summary": {"trocas": [{"table": "t"}, "z", None], "fallback_tabelas": "t"},
                       "falhas": [{"motivo": "m"}, None, "s"]}),
        (None, "prd", _resumo(falhas=[{"table": "sem_horario", "motivo": "m"}])),
        (H(10), "prd", _resumo(trocas=[_troca("ok", 5.0)])),
    ]
    info = collect_troca(lixo)
    assert info is not None
    assert ("prd", "ok") in info.trocas
    assert all(f.table != "sem_horario" for f in info.falhas)  # sem horário não há como ordenar


def test_blocos_do_log_le_os_dois_ambientes_e_ignora_ausente_nulo_e_nao_mapa():
    payload = {"sync_prd": _resumo(), "sync_dev": None, "outro": 1}
    assert [e for e, _ in blocos_do_log(payload)] == ["prd"]
    assert blocos_do_log({"sync_prd": "x", "sync_dev": 3}) == []
    assert blocos_do_log({}) == []


def test_nomes_das_falhas_para_o_detalhe_do_parcial():
    blocos = [
        ("prd", _resumo(falhas=[{"table": "fact_fixtures", "motivo": "lock_timeout"},
                                {"table": "fact_h2h", "motivo": "formato_divergente"}])),
        ("dev", _resumo()),
    ]
    assert nomes_das_falhas(blocos) == "fact_fixtures (lock_timeout), fact_h2h (formato_divergente)"
    assert nomes_das_falhas([("prd", {"falhas": None})]) == ""


# ------------------------------------------------------------------
# Fiação em daily_summary
# ------------------------------------------------------------------
@pytest.fixture
def daily_summary():
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


class _Entry:
    def __init__(self, payload, timestamp):
        self.payload = payload
        self.timestamp = timestamp


class _FakeLogging:
    def __init__(self, entries):
        self._entries = entries

    def list_entries(self, **kwargs):
        return iter(self._entries)


def _conclusao(**extra):
    base = {
        "message": "Workflow futebol-sync concluído",
        "workflow_name": "workflow_futebol_sync",
        "status": "SUCCESS",
        "duration_seconds": 640.0,
        "failed_services": [],
        "failed_count": 0,
    }
    base.update(extra)
    return base


def _agg(daily_summary, *pares):
    agg = defaultdict(daily_summary.WFAgg)
    daily_summary.collect_from_logging(
        _FakeLogging([_Entry(p, t) for p, t in pares]), H(3),
        datetime(2026, 10, 2, 3, tzinfo=timezone.utc), agg,
    )
    return agg


def _registros(agg):
    return [r for a in agg.values() for r in a.troca_sync]


def test_log_de_conclusao_com_o_bloco_gera_registro_por_ambiente(daily_summary):
    r = _resumo(trocas=[_troca("fact_fixtures", 9.0)])
    agg = _agg(daily_summary, (_conclusao(sync_prd=r, sync_dev=None), H(14)))
    assert _registros(agg) == [(H(14), "prd", r)]


def test_log_sem_o_bloco_ou_com_bloco_nulo_nao_gera_registro_nem_vermelho(daily_summary):
    agg = _agg(daily_summary, (_conclusao(), H(13)), (_conclusao(sync_prd=None, sync_dev=None), H(14)))
    assert _registros(agg) == []
    assert agg["workflow-futebol-sync"].success == 2


def test_parcial_por_falha_de_troca_nomeia_a_tabela_no_detalhe(daily_summary):
    r = _resumo(falhas=[{"table": "fact_fixtures", "motivo": "lock_timeout"}])
    agg = _agg(daily_summary, (_conclusao(
        status="PARTIAL_FAILURE", failed_services=["sync-bq-to-postgres[futebol/prd]"],
        failed_count=1, sync_prd=r), H(14)))
    (_, status, detalhe), = agg["workflow-futebol-sync"].failures
    assert status == "PARTIAL_FAILURE"
    assert "sync-bq-to-postgres[futebol/prd]" in detalhe
    assert "fact_fixtures (lock_timeout)" in detalhe


def test_parcial_sem_bloco_mantem_o_detalhe_de_antes(daily_summary):
    agg = _agg(daily_summary, (_conclusao(
        status="PARTIAL_FAILURE", failed_services=["sync-bq-to-postgres[futebol/dev]"], failed_count=1), H(14)))
    (_, _, detalhe), = agg["workflow-futebol-sync"].failures
    assert detalhe == "sync-bq-to-postgres[futebol/dev]"


def test_a_secao_entra_no_email_e_nao_ha_token_novo_no_assunto(daily_summary):
    """Fallback e aviso não são falha de workflow; a falha de troca já vira [FALHAS] pelo parcial."""
    r = _resumo(trocas=[_troca("fact_fixtures", 9.0)], fallback_tabelas=["fact_h2h"])
    agg = _agg(daily_summary, (_conclusao(sync_prd=r), H(14)))
    info = collect_troca(_registros(agg))
    assunto, html = daily_summary.build_html(DIA, agg, troca_sync=info)
    assert assunto.startswith("[OK]")
    assert "fact_fixtures" in html and "fact_h2h" in html
    assert html.endswith("</div>")


def test_build_html_sem_o_argumento_mantem_o_email_de_antes(daily_summary):
    _, html = daily_summary.build_html(DIA, {})
    assert "troca" not in html.lower()
