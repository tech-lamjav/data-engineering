"""Snapshot congelado do universo de odds anterior ao corte (DE#109, história 57 da #112).

Antes de qualquer corte de retenção em PRD, o data-engineering congela no BigQuery uma cópia
datada de `fact_odds_snapshot`: a janela `daily` é recapturada e SOBRESCRITA no próprio BigQuery
(o dbt faz latest-wins a cada rebuild), então "os números já enviados" só são refazíveis se
alguém tiver guardado a tabela de antes. O script é dry-run por padrão e só grava com `--apply`
(ou SNAPSHOT_APPLY=1); aqui se prova o que é puro e o contrato com o cliente do BigQuery, sem BigQuery.
"""
import importlib
from datetime import date

import pytest
from google.api_core.exceptions import NotFound

from src.monitoring import snapshot_odds as snap

HOJE = date(2026, 10, 2)
from src.config import BIGQUERY_PROJECT_ID  # noqa: E402

PROJETO, DATASET, TABELA = BIGQUERY_PROJECT_ID, "futebol", "fact_odds_snapshot"


class _Tabela:
    def __init__(self, num_rows=4_300_000, num_bytes=706_900_000, modified="m"):
        self.num_rows, self.num_bytes, self.modified = num_rows, num_bytes, modified
        self.description, self.labels = None, {}


class BQFalso:
    def __init__(self, destino_existe=False, origem=None):
        self.tabelas = {f"{PROJETO}.{DATASET}.{TABELA}": origem or _Tabela()}
        if destino_existe:
            self.tabelas[f"{PROJETO}.{DATASET}.{TABELA}_pre_corte_20261002"] = _Tabela()
        self.copias = []
        self.atualizacoes = []

    def get_table(self, ref):
        if ref not in self.tabelas:
            raise NotFound(ref)
        return self.tabelas[ref]

    def copy_table(self, origem, destino, job_config=None):
        self.copias.append((origem, destino, job_config))
        self.tabelas[destino] = _Tabela(self.tabelas[origem].num_rows)

        class _Job:
            def result(self_):
                return None

        return _Job()

    def update_table(self, tabela, campos):
        self.atualizacoes.append((tabela, campos))
        return tabela


# ------------------------------------------------------------------
# Puro
# ------------------------------------------------------------------
def test_o_nome_do_snapshot_leva_a_data_e_e_estavel():
    assert snap.nome_do_snapshot(TABELA, HOJE) == "fact_odds_snapshot_pre_corte_20261002"


def test_pedido_de_apply_vem_do_flag_ou_da_variavel_e_o_padrao_e_dry_run():
    assert snap.pediu_apply([], {}) is False
    assert snap.pediu_apply(["--apply"], {}) is True
    assert snap.pediu_apply([], {"SNAPSHOT_APPLY": "1"}) is True
    assert snap.pediu_apply([], {"SNAPSHOT_APPLY": "0"}) is False
    assert snap.pediu_apply([], {"SNAPSHOT_APPLY": ""}) is False


def test_argumento_desconhecido_e_recusado_para_um_typo_nao_virar_dry_run_silencioso():
    with pytest.raises(ValueError, match="--aply"):
        snap.pediu_apply(["--aply"], {})


# ------------------------------------------------------------------
# Dry-run por padrão: não grava nada
# ------------------------------------------------------------------
def test_dry_run_planeja_e_nao_copia_nem_atualiza_nada():
    bq = BQFalso()

    relatorio = snap.executa(bq, PROJETO, DATASET, TABELA, HOJE, aplicar=False)

    assert relatorio.aplicado is False and relatorio.destino_ja_existe is False
    assert relatorio.destino == f"{PROJETO}.{DATASET}.fact_odds_snapshot_pre_corte_20261002"
    assert relatorio.linhas_origem == 4_300_000
    assert bq.copias == [] and bq.atualizacoes == []


def test_apply_copia_com_write_empty_e_carimba_descricao_e_rotulo():
    bq = BQFalso()

    relatorio = snap.executa(bq, PROJETO, DATASET, TABELA, HOJE, aplicar=True)

    assert relatorio.aplicado is True
    (origem, destino, cfg), = bq.copias
    assert origem == f"{PROJETO}.{DATASET}.{TABELA}"
    assert destino.endswith("fact_odds_snapshot_pre_corte_20261002")
    # nunca sobrescreve: WRITE_EMPTY falha se o destino já tem dados
    assert cfg.write_disposition == "WRITE_EMPTY"
    (tabela, campos), = bq.atualizacoes
    assert "congelad" in tabela.description.lower() and "2026-10-02" in tabela.description
    assert tabela.labels["congelada"] == "true"
    assert set(campos) == {"description", "labels"}


def test_destino_que_ja_existe_recusa_e_nao_toca_em_nada_mesmo_com_apply():
    bq = BQFalso(destino_existe=True)

    with pytest.raises(snap.SnapshotJaExiste, match="20261002"):
        snap.executa(bq, PROJETO, DATASET, TABELA, HOJE, aplicar=True)

    assert bq.copias == []


def test_origem_inexistente_falha_alto_no_dry_run_tambem():
    bq = BQFalso()
    del bq.tabelas[f"{PROJETO}.{DATASET}.{TABELA}"]

    with pytest.raises(NotFound):
        snap.executa(bq, PROJETO, DATASET, TABELA, HOJE, aplicar=False)


# ------------------------------------------------------------------
# O script: dry-run por padrão, código de saída
# ------------------------------------------------------------------
@pytest.fixture
def script(monkeypatch):
    mod = importlib.import_module("scripts.snapshot_odds_pre_corte")
    return mod


def test_o_script_sem_flag_e_dry_run_e_sai_com_zero(script, monkeypatch, capsys):
    bq = BQFalso()
    monkeypatch.setattr(script, "cliente_bigquery", lambda: bq)
    monkeypatch.setattr(script, "hoje", lambda: HOJE)

    codigo = script.main([], {})

    assert codigo == 0 and bq.copias == []
    saida = capsys.readouterr().out
    assert "DRY-RUN" in saida and "--apply" in saida


def test_o_script_com_apply_grava_e_avisa(script, monkeypatch, capsys):
    bq = BQFalso()
    monkeypatch.setattr(script, "cliente_bigquery", lambda: bq)
    monkeypatch.setattr(script, "hoje", lambda: HOJE)

    codigo = script.main(["--apply"], {})

    assert codigo == 0 and len(bq.copias) == 1
    assert "CRIADO" in capsys.readouterr().out


def test_o_script_recusa_destino_existente_com_codigo_1(script, monkeypatch, capsys):
    bq = BQFalso(destino_existe=True)
    monkeypatch.setattr(script, "cliente_bigquery", lambda: bq)
    monkeypatch.setattr(script, "hoje", lambda: HOJE)

    assert script.main(["--apply"], {}) == 1
    assert bq.copias == []
