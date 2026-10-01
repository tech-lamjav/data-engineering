"""Smoke de IAM do sync de DEV (DE#106, histórias 32 da #106 e 51 da #112).

A conta de runtime do sync precisa de `bigquery.jobs.create` (jobUser) e de leitura dos
datasets para a retenção de DEV, que lê por query job. O smoke prova as duas coisas ANTES do
deploy, só com dry-run (grátis, nenhuma linha lida, nenhum TRUNCATE), sob a conta de runtime
(impersonada). Aqui os testes usam um cliente falso; o BigQuery real só se prova rodando o
script sob a conta, passo do runbook do PR.
"""
import pytest
from google.api_core.exceptions import Forbidden
from google.auth.exceptions import RefreshError
from google.cloud import bigquery

from src.monitoring.smoke_iam_sync import executa_smoke, codigo_de_saida
from src.sync.alvo import resolve_alvo_sync
from src.sync.retencao import resolve_regra_retencao

_TIPOS = {
    "season": "INTEGER",
    "snapshot_date": "DATE",
    "collection_timestamp": "TIMESTAMP",
    "collection_date": "DATE",
    "fixture_id": "INTEGER",
}


def _tabelas_com_regra():
    _, _, tabelas = resolve_alvo_sync("futebol")
    return [t for t in tabelas if resolve_regra_retencao("futebol", "dev", t) is not None]


class _Job:
    def result(self):
        return iter(())


class _Tabela:
    def __init__(self, nome):
        regra = resolve_regra_retencao("futebol", "dev", nome)
        colunas = {regra["column"], regra.get("partition_column"), "payload"} - {None}
        self.schema = [
            bigquery.SchemaField(c, _TIPOS.get(c, "STRING")) for c in sorted(colunas)
        ]
        self.num_bytes = 10 * 1024 * 1024


class _BQ:
    """Cliente falso: registra tudo e falha onde mandado."""

    def __init__(self, nega_job=None, nega_tabela=()):
        self.queries = []  # (sql, job_config)
        self.tabelas_lidas = []
        self._nega_job = nega_job
        self._nega_tabela = set(nega_tabela)

    def query(self, sql, job_config=None):
        if self._nega_job is not None:
            raise self._nega_job
        self.queries.append((sql, job_config))
        return _Job()

    def get_table(self, ref):
        nome = ref.split(".")[-1]
        self.tabelas_lidas.append(nome)
        if nome in self._nega_tabela:
            raise Forbidden("sem READER no dataset")
        return _Tabela(nome)


def test_tudo_liberado_so_faz_dry_run_e_cobre_toda_tabela_com_regra():
    bq = _BQ()

    conferidas, falhas = executa_smoke(bq)

    assert falhas == []
    esperadas = _tabelas_com_regra()
    assert conferidas == len(esperadas) == 11
    assert sorted(bq.tabelas_lidas) == sorted(esperadas)
    # pré-voo (SELECT 1) + uma leitura filtrada por tabela com regra
    assert len(bq.queries) == 1 + len(esperadas)
    # nada pode ler de verdade: todo job é dry-run
    assert all(cfg.dry_run is True for _, cfg in bq.queries)


def test_a_leitura_filtrada_do_smoke_e_a_mesma_que_o_sync_faz():
    bq = _BQ()

    executa_smoke(bq)

    sqls = [sql for sql, _ in bq.queries if "fact_odds_snapshot" in sql]
    assert len(sqls) == 1
    assert "`collection_timestamp` >= @corte" in sqls[0]
    assert "`collection_date` >= @corte_particao" in sqls[0]
    assert "`smartbetting-dados.futebol.fact_odds_snapshot`" in sqls[0]


def test_sem_jobuser_falha_no_pre_voo_e_nao_segue():
    bq = _BQ(nega_job=Forbidden("sem bigquery.jobs.create"))

    conferidas, falhas = executa_smoke(bq)

    assert conferidas == 0
    assert len(falhas) == 1
    assert "bigquery.jobs.create" in falhas[0]
    assert bq.tabelas_lidas == []


def test_sem_leitura_do_dataset_aponta_a_tabela_e_continua_nas_outras():
    bq = _BQ(nega_tabela={"fact_insumos_medidos"})

    conferidas, falhas = executa_smoke(bq)

    assert len(falhas) == 1
    assert "fact_insumos_medidos" in falhas[0]
    assert "Forbidden" in falhas[0]
    assert conferidas == 10  # as outras dez passaram


def test_erro_de_credencial_nao_vira_falha_de_permissao():
    bq = _BQ(nega_job=RefreshError("impersonation negada"))

    with pytest.raises(RefreshError):
        executa_smoke(bq)


def test_codigo_de_saida_0_verde_1_permissao():
    assert codigo_de_saida(11, []) == 0
    assert codigo_de_saida(10, ["fact_insumos_medidos: Forbidden"]) == 1
    # nada conferido não é verde
    assert codigo_de_saida(0, []) == 1
