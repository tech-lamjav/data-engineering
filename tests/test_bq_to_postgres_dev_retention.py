"""Testes da retenção de DEV no sync BQ->Postgres (DE#75/#76/#77, refeitos na DE#106).

Mock total de infra (mesmo estilo de tests/test_bq_to_postgres_sync.py) — sem tocar rede/DB real.

O QUE MUDOU NA DE#106: o corte deixou de rodar em Python depois de ler a tabela inteira e passou
a rodar NO BIGQUERY (query job parametrizado, `src.sync.filtro_bq`). O BigQuery falso destes
testes já entrega o conjunto filtrado, e o que se afirma é:
- o que chega ao BigQuery (o parâmetro do corte, a lista de fixtures elegíveis, a temporada);
- que o caminho de carga grava só o que chega e nada mais (nenhum filtro de segunda mão em Python);
- que PRD e tabela sem regra nunca abrem query job (custo e IAM);
- que a falta de permissão para criar job derruba o passe ANTES de qualquer TRUNCATE.

O SQL em si só se prova contra o BigQuery real (dry-run e contagens, coladas no PR da DE#106).
O fake de `fact_fixtures` avalia os parâmetros da consulta sobre uma lista de (fixture_id,
kickoff) — não devolve sempre o mesmo conjunto —, para a faixa -30/+14 ser afirmada pelo
comportamento: uma fixture a +20 dias fica de fora, uma a +10 entra, uma a -40 fica de fora.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

try:
    from src.sync import bq_to_postgres as mod
except Exception as e:  # pragma: no cover
    pytest.skip(f"src.sync.bq_to_postgres não importável: {e}", allow_module_level=True)

_FUTEBOL = dict(dataset="futebol", schema="futebol")
_NOW = datetime.now(timezone.utc)


def _field(name, mode="NULLABLE", field_type="STRING"):
    f = MagicMock()
    f.name = name
    f.mode = mode
    f.field_type = field_type
    return f


def _linhas(rows):
    out = []
    for r in rows:
        rm = MagicMock()
        rm.__getitem__.side_effect = lambda c, _r=r: _r[c]
        out.append(rm)
    return out


class _FakeBq:
    """BigQuery falso. `rows` é o que `list_rows` entregaria (a tabela inteira);
    `query_rows` é o que o query job entrega (o conjunto JÁ filtrado pelo BigQuery)."""

    def __init__(
        self, rows, columns, modified=_NOW, field_types=None, query_rows=None,
        query_error=None, num_bytes=50_000_000,
    ):
        field_types = field_types or {}
        self.schema = [_field(c, field_type=field_types.get(c, "STRING")) for c in columns]
        self.query_calls = []
        self.query_rows = rows if query_rows is None else query_rows
        self.query_error = query_error
        self.list_rows_iterations = 0

        def _itera_tabela_inteira():
            self.list_rows_iterations += 1
            return iter(_linhas(rows))

        row_iter = MagicMock()
        row_iter.schema = self.schema
        row_iter.table.modified = modified
        row_iter.table.num_bytes = num_bytes
        row_iter.__iter__.side_effect = _itera_tabela_inteira
        self.row_iter = row_iter
        self.list_rows = MagicMock(return_value=row_iter)
        self.query = MagicMock(side_effect=self._query)

    def _query(self, sql, job_config=None):
        self.query_calls.append((sql, job_config))
        if self.query_error is not None:
            raise self.query_error
        resultado = MagicMock(name="RowIterator")
        resultado.schema = self.schema
        resultado.__iter__.side_effect = lambda: iter(_linhas(self.query_rows))
        job = MagicMock()
        job.result.return_value = resultado
        return job

    def parametros(self):
        _, cfg = self.query_calls[-1]
        return {p.name: p for p in cfg.query_parameters}


def _make_bq(rows, columns, **kw):
    return _FakeBq(rows, columns, **kw)


class _FakeCopy:
    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, row):
        self.rows.append(list(row))


class _FakeCursor:
    """Cursor falso: fetchone serve _read_last_synced; fetchall serve o lookup de fixtures
    elegíveis. O lookup AVALIA os parâmetros da consulta sobre `fixtures` (lista de
    (fixture_id, kickoff)): janela aberta só para trás com 1 parâmetro, faixa fechada com 2."""

    def __init__(self, conn):
        self._conn = conn
        self._last_sql = None
        self._last_params = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))
        self._last_sql, self._last_params = sql, params
        if "fact_fixtures" in sql and "SELECT fixture_id" in sql:
            self._conn.fixture_lookup_calls.append((sql, params))

    def fetchone(self):
        last = self._conn.last_synced
        return (last,) if last is not None else None

    def fetchall(self):
        params = self._last_params or ()
        inicio = params[0]
        fim = params[1] if len(params) > 1 else None
        return [
            (fid,)
            for fid, kickoff in self._conn.fixtures
            if kickoff >= inicio and (fim is None or kickoff <= fim)
        ]

    def copy(self, sql):
        self._conn.copy_obj.sql = sql
        return self._conn.copy_obj


class _FakeConn:
    def __init__(self, copy_obj, last_synced=None, fixtures=None):
        self.copy_obj = copy_obj
        self.last_synced = last_synced
        self.fixtures = fixtures or []
        self.executed: list = []
        self.fixture_lookup_calls: list = []
        self.committed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True

    def truncou(self):
        return any("TRUNCATE" in sql for sql, _ in self.executed)


_COLUNAS_ODDS = ["fixture_id", "collection_timestamp", "collection_date"]
_TIPOS_ODDS = {"collection_timestamp": "TIMESTAMP", "collection_date": "DATE"}


def _sync(bq, conn, tabela, env="dev", sport="futebol"):
    return mod._sync_one_table(
        bq, conn, tabela, tables_ordered=[tabela], env=env, sport=sport, **_FUTEBOL
    )


# ------------------------------------------------------------------
# Retenção de coleta: o corte de 7 dias chega ao BigQuery como parâmetro
# ------------------------------------------------------------------
def test_dev_odds_so_grava_o_que_o_bigquery_devolveu_e_nao_refiltra_em_python():
    do_job = [
        {"fixture_id": 1, "collection_timestamp": _NOW - timedelta(days=2),
         "collection_date": (_NOW - timedelta(days=2)).date()},
        # Velha demais para a retenção, mas o BigQuery a entregou: o caminho de carga
        # grava o que chega. O corte é do BigQuery, não de um segundo filtro em Python.
        {"fixture_id": 2, "collection_timestamp": _NOW - timedelta(days=30),
         "collection_date": (_NOW - timedelta(days=30)).date()},
    ]
    bq = _make_bq([], _COLUNAS_ODDS, query_rows=do_job, field_types=_TIPOS_ODDS)
    copia = _FakeCopy()

    result = _sync(bq, _FakeConn(copia), "fact_odds_snapshot")

    assert result["rows"] == 2
    assert [r[0] for r in copia.rows] == [1, 2]


def test_dev_odds_corte_de_coleta_e_de_7_dias_e_nao_de_14():
    """Uma linha de ~10 dias entrava com 14 e sai com 7: o parâmetro que o BigQuery recebe
    tem de ser agora - 7 dias (a linha de 10 dias fica de fora do job, não do Python)."""
    bq = _make_bq([], _COLUNAS_ODDS, field_types=_TIPOS_ODDS)

    _sync(bq, _FakeConn(_FakeCopy()), "fact_odds_snapshot")

    corte = bq.parametros()["corte"]
    assert corte.type_ == "TIMESTAMP"
    sete_dias = _NOW - timedelta(days=7)
    assert abs((corte.value - sete_dias).total_seconds()) < 60
    assert corte.value > _NOW - timedelta(days=10)


def test_dev_odds_poda_particao_por_collection_date_com_folga_de_um_dia():
    """`fact_odds_snapshot` é particionada por collection_date: sem um corte na coluna de
    partição o job lê a tabela toda. A folga de 1 dia cobre uma collection_date derivada em
    outro fuso, que não pode perder linha que o corte por timestamp deixaria entrar."""
    bq = _make_bq([], _COLUNAS_ODDS, field_types=_TIPOS_ODDS)

    _sync(bq, _FakeConn(_FakeCopy()), "fact_odds_snapshot")

    p = bq.parametros()["corte_particao"]
    assert p.type_ == "DATE"
    assert p.value <= (_NOW - timedelta(days=7)).date() - timedelta(days=1)
    assert p.value >= (_NOW - timedelta(days=9)).date()


def test_dev_desfalques_corte_por_data_chega_como_parametro_date():
    bq = _make_bq([], ["fixture_id", "snapshot_date"], field_types={"snapshot_date": "DATE"})

    _sync(bq, _FakeConn(_FakeCopy()), "fact_injuries_snapshot")

    corte = bq.parametros()["corte"]
    assert corte.type_ == "DATE"
    assert corte.value == (_NOW - timedelta(days=7)).date()


# ------------------------------------------------------------------
# Retenção por temporada (regras de 09/09, intocadas no config)
# ------------------------------------------------------------------
def test_dev_season_manda_a_temporada_corrente_ao_bigquery():
    from src.config import FUTEBOL_DEV_CURRENT_SEASON

    do_job = [{"fixture_id": 1, "season": FUTEBOL_DEV_CURRENT_SEASON}]
    bq = _make_bq([], ["fixture_id", "season"], query_rows=do_job,
                  field_types={"season": "INTEGER"})
    copia = _FakeCopy()

    result = _sync(bq, _FakeConn(copia), "fact_fixture_player_stats")

    assert bq.parametros()["temporada"].value == FUTEBOL_DEV_CURRENT_SEASON
    assert result["rows"] == 1 and copia.rows[0][0] == 1


# ------------------------------------------------------------------
# Retenção de produto: faixa de kickoff -30/+14 por fixture
# ------------------------------------------------------------------
def _fixtures_de_teste():
    return [
        (10, _NOW + timedelta(days=10)),   # entra (dentro dos 14 à frente)
        (20, _NOW + timedelta(days=20)),   # fora (mais de 14 à frente)
        (30, _NOW - timedelta(days=40)),   # fora (mais de 30 para trás)
        (40, _NOW - timedelta(days=5)),    # entra
        (50, _NOW + timedelta(days=13)),   # entra
        (60, _NOW - timedelta(days=29)),   # entra
    ]


@pytest.mark.parametrize(
    "tabela",
    [
        "fact_insumos_medidos",
        "int_futebol_premissas_1x2",
        "int_futebol_premissas_ou",
        "int_futebol_premissas_ah",
        "int_futebol_premissas_btts",
        "int_futebol_premissas_dc",
        "fact_value_opportunities_hist",
    ],
)
def test_dev_tabela_de_produto_manda_ao_bigquery_so_as_fixtures_de_menos_30_a_mais_14(tabela):
    bq = _make_bq([], ["fixture_id", "valor"], field_types={"fixture_id": "INTEGER"})
    conn = _FakeConn(_FakeCopy(), fixtures=_fixtures_de_teste())

    _sync(bq, conn, tabela)

    ids = bq.parametros()["ids"]
    assert ids.array_type == "INT64"
    assert sorted(ids.values) == [10, 40, 50, 60]


def test_dev_fixture_a_20_dias_a_frente_e_a_40_para_tras_ficam_fora_a_10_entra():
    bq = _make_bq([], ["fixture_id"], field_types={"fixture_id": "INTEGER"})
    conn = _FakeConn(_FakeCopy(), fixtures=[(1, _NOW + timedelta(days=20)),
                                            (2, _NOW + timedelta(days=10)),
                                            (3, _NOW - timedelta(days=40))])

    _sync(bq, conn, "fact_insumos_medidos")

    assert list(bq.parametros()["ids"].values) == [2]


def test_dev_produto_sem_nenhuma_fixture_na_faixa_manda_lista_vazia_e_grava_zero():
    bq = _make_bq([], ["fixture_id"], query_rows=[], field_types={"fixture_id": "INTEGER"})
    conn = _FakeConn(_FakeCopy(), fixtures=[(3, _NOW - timedelta(days=90))])

    result = _sync(bq, conn, "fact_insumos_medidos")

    assert list(bq.parametros()["ids"].values) == []
    assert result["rows"] == 0


def test_dev_produto_consulta_fact_fixtures_uma_unica_vez_e_pela_coluna_kickoff_utc():
    """O SELECT contra fact_fixtures roda 1x por tabela, não 1x por linha. Coluna real é
    `kickoff_utc` (dbt_futebol/models/marts/fact_fixtures.sql) — regressão da DE#77."""
    bq = _make_bq([], ["fixture_id"], field_types={"fixture_id": "INTEGER"})
    conn = _FakeConn(_FakeCopy(), fixtures=_fixtures_de_teste())

    _sync(bq, conn, "fact_insumos_medidos")

    assert len(conn.fixture_lookup_calls) == 1
    lookup_sql, params = conn.fixture_lookup_calls[0]
    assert "kickoff_utc >=" in lookup_sql and "kickoff_utc <=" in lookup_sql
    assert params[1] - params[0] == timedelta(days=44)


def test_dev_produto_grava_so_o_que_o_bigquery_devolveu():
    do_job = [{"fixture_id": 10, "valor": 1.0}, {"fixture_id": 40, "valor": 2.0}]
    bq = _make_bq([], ["fixture_id", "valor"], query_rows=do_job,
                  field_types={"fixture_id": "INTEGER"})
    copia = _FakeCopy()

    result = _sync(bq, _FakeConn(copia, fixtures=_fixtures_de_teste()), "fact_insumos_medidos")

    assert result["rows"] == 2
    assert [r[0] for r in copia.rows] == [10, 40]


# ------------------------------------------------------------------
# PRD e tabela sem regra: byte-idêntico ao de antes, nunca abre query job
# ------------------------------------------------------------------
def test_prd_le_a_tabela_inteira_por_list_rows_e_nunca_abre_query_job():
    rows = [
        {"fixture_id": 1, "collection_timestamp": _NOW - timedelta(days=2)},
        {"fixture_id": 2, "collection_timestamp": _NOW - timedelta(days=200)},
    ]
    bq = _make_bq(rows, ["fixture_id", "collection_timestamp"])
    copia = _FakeCopy()

    result = _sync(bq, _FakeConn(copia), "fact_odds_snapshot", env="prd")

    assert result["rows"] == 2
    assert bq.query.call_count == 0


def test_prd_tabela_de_produto_nao_faz_lookup_de_fixtures_nem_abre_query_job():
    rows = [{"fixture_id": 10}, {"fixture_id": 20}]
    bq = _make_bq(rows, ["fixture_id"])
    conn = _FakeConn(_FakeCopy())

    result = _sync(bq, conn, "fact_insumos_medidos", env="prd")

    assert result["rows"] == 2
    assert bq.query.call_count == 0
    assert conn.fixture_lookup_calls == []


def test_dev_tabela_sem_regra_le_por_list_rows_e_nunca_abre_query_job():
    rows = [{"team_id": 1, "nome": "a"}, {"team_id": 2, "nome": "b"}]
    bq = _make_bq(rows, ["team_id", "nome"])

    result = _sync(bq, _FakeConn(_FakeCopy()), "dim_teams")

    assert result["rows"] == 2
    assert bq.query.call_count == 0


def test_dev_com_regra_nao_le_a_tabela_inteira_por_list_rows():
    """O ganho da DE#106: o DEV deixa de ler as linhas que vai descartar."""
    rows = [
        {"fixture_id": i, "collection_timestamp": _NOW, "collection_date": _NOW.date()}
        for i in range(1000)
    ]
    bq = _make_bq(rows, _COLUNAS_ODDS, query_rows=rows[:3], field_types=_TIPOS_ODDS)

    result = _sync(bq, _FakeConn(_FakeCopy()), "fact_odds_snapshot")

    assert result["rows"] == 3
    assert bq.list_rows_iterations == 0


# ------------------------------------------------------------------
# Skip-if-unchanged continua decidindo antes de qualquer custo
# ------------------------------------------------------------------
def test_dev_skip_if_unchanged_interrompe_antes_do_query_job():
    modified = _NOW - timedelta(days=1)
    bq = _make_bq([], ["fixture_id", "collection_timestamp"], modified=modified)
    copia = _FakeCopy()

    result = _sync(bq, _FakeConn(copia, last_synced=modified), "fact_odds_snapshot")

    assert result["skipped"] is True
    assert bq.query.call_count == 0
    assert copia.rows == []


# ------------------------------------------------------------------
# Falha de IAM / teto de bytes: derruba o passe com a tabela de destino intacta
# ------------------------------------------------------------------
def test_dev_sem_permissao_para_criar_job_levanta_antes_do_truncate():
    class Forbidden(Exception):
        pass

    bq = _make_bq([], _COLUNAS_ODDS, query_error=Forbidden("403 bigquery.jobs.create"),
                  field_types=_TIPOS_ODDS)
    conn = _FakeConn(_FakeCopy())

    with pytest.raises(Forbidden):
        _sync(bq, conn, "fact_odds_snapshot")

    assert not conn.truncou()


def test_dev_o_job_leva_teto_de_bytes_proporcional_ao_tamanho_da_tabela():
    tamanho = 700_000_000
    bq = _make_bq([], _COLUNAS_ODDS, num_bytes=tamanho, field_types=_TIPOS_ODDS)

    _sync(bq, _FakeConn(_FakeCopy()), "fact_odds_snapshot")

    _, cfg = bq.query_calls[-1]
    assert tamanho <= cfg.maximum_bytes_billed <= 3 * tamanho


def test_dev_tabela_pequena_ganha_teto_minimo_e_nao_zero():
    bq = _make_bq([], ["fixture_id", "snapshot_date"], num_bytes=1_000,
                  field_types={"snapshot_date": "DATE"})

    _sync(bq, _FakeConn(_FakeCopy()), "fact_injuries_snapshot")

    _, cfg = bq.query_calls[-1]
    assert cfg.maximum_bytes_billed >= 10 * 1024 * 1024


def test_dev_coluna_da_regra_fora_do_schema_falha_explicito_antes_do_job():
    bq = _make_bq([], ["fixture_id", "outra_coluna"])
    conn = _FakeConn(_FakeCopy())

    with pytest.raises(ValueError, match="collection_timestamp"):
        _sync(bq, conn, "fact_odds_snapshot")

    assert bq.query.call_count == 0 and not conn.truncou()


# ------------------------------------------------------------------
# Ordem de execução explícita: tabela de produto sem fact_fixtures na mesma run falha,
# em vez de produzir filtro vazio.
# ------------------------------------------------------------------
def test_run_sync_falha_se_tabela_de_produto_sem_fact_fixtures_na_mesma_run_em_dev():
    with pytest.raises(RuntimeError, match="fact_fixtures"):
        mod._assert_dev_retention_order("futebol", "dev", ["fact_insumos_medidos"])


@pytest.mark.parametrize("tabela", ["int_futebol_premissas_dc", "fact_value_opportunities_hist"])
def test_run_sync_falha_para_cada_tabela_de_produto_sem_fact_fixtures(tabela):
    with pytest.raises(RuntimeError):
        mod._assert_dev_retention_order("futebol", "dev", [tabela])


def test_run_sync_ok_se_produto_e_fact_fixtures_na_mesma_run_em_dev():
    mod._assert_dev_retention_order(
        "futebol", "dev", ["fact_fixtures", "fact_insumos_medidos"]
    )  # não levanta


def test_run_sync_tabela_de_coleta_nao_exige_fact_fixtures():
    mod._assert_dev_retention_order("futebol", "dev", ["fact_odds_snapshot"])  # não levanta


def test_run_sync_prd_nunca_falha_por_ordem():
    mod._assert_dev_retention_order(
        "futebol", "prd", ["fact_insumos_medidos"]
    )  # não levanta — regra nunca se aplica em PRD
