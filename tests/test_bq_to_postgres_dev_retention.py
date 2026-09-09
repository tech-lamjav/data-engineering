"""Testes do filtro de retenção de DEV no sync BQ->Postgres (DE#75/#76/#77).

Mock total de infra (mesmo estilo de tests/test_bq_to_postgres_sync.py) — sem tocar
rede/DB real. Cobre as 3 formas de regra ('timestamp_days', 'season',
'fixture_window'), o passthrough em PRD e em tabela sem regra configurada, e a
checagem explícita de ordem de execução do DE#77.
"""
from datetime import datetime, timedelta, timezone
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


def _make_bq(rows, columns, modified=_NOW):
    schema = [_field(c) for c in columns]
    bq_rows = []
    for r in rows:
        rm = MagicMock()
        rm.__getitem__.side_effect = lambda c, _r=r: _r[c]
        bq_rows.append(rm)
    row_iter = MagicMock()
    row_iter.schema = schema
    row_iter.table.modified = modified
    row_iter.__iter__.return_value = iter(bq_rows)
    bq = MagicMock()
    bq.list_rows.return_value = row_iter
    return bq


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
    """Cursor falso: fetchone serve _read_last_synced; fetchall serve o lookup de
    fixtures elegíveis (DE#77). `executed` e `fixture_lookup_calls` são listas
    COMPARTILHADAS com a conexão — cada `.cursor()` devolve uma instância nova, mas
    todas escrevem no mesmo lugar, permitindo contar chamadas pela conexão inteira.
    """

    def __init__(self, copy_obj, last_synced, executed, eligible_fixture_ids, fixture_lookup_calls):
        self._copy = copy_obj
        self._last_synced = last_synced
        self._executed = executed
        self._eligible_fixture_ids = eligible_fixture_ids or set()
        self._fixture_lookup_calls = fixture_lookup_calls
        self._last_sql = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._executed.append((sql, params))
        self._last_sql = sql
        if "fact_fixtures" in sql and "SELECT fixture_id" in sql:
            self._fixture_lookup_calls.append((sql, params))

    def fetchone(self):
        return (self._last_synced,) if self._last_synced is not None else None

    def fetchall(self):
        return [(fid,) for fid in self._eligible_fixture_ids]

    def copy(self, sql):
        self._copy.sql = sql
        return self._copy


class _FakeConn:
    def __init__(self, copy_obj, last_synced=None, eligible_fixture_ids=None):
        self._copy = copy_obj
        self._last_synced = last_synced
        self._eligible_fixture_ids = eligible_fixture_ids
        self.executed: list = []
        self.fixture_lookup_calls: list = []
        self.committed = False

    def cursor(self):
        return _FakeCursor(
            self._copy,
            self._last_synced,
            self.executed,
            self._eligible_fixture_ids,
            self.fixture_lookup_calls,
        )

    def commit(self):
        self.committed = True


# ------------------------------------------------------------------
# 'timestamp_days' — fact_odds_snapshot (collection_timestamp)
# ------------------------------------------------------------------
def test_dev_timestamp_days_fact_odds_snapshot_so_linhas_recentes():
    columns = ["fixture_id", "collection_timestamp"]
    dentro = _NOW - timedelta(days=5)
    fora = _NOW - timedelta(days=20)
    rows = [
        {"fixture_id": 1, "collection_timestamp": dentro},
        {"fixture_id": 2, "collection_timestamp": fora},
    ]
    bq = _make_bq(rows, columns)
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy)

    result = mod._sync_one_table(
        bq, conn, "fact_odds_snapshot", tables_ordered=["fact_odds_snapshot"],
        env="dev", sport="futebol", **_FUTEBOL,
    )

    assert result["rows"] == 1
    assert [r[0] for r in fake_copy.rows] == [1]


def test_prd_timestamp_days_nao_filtra_nada():
    """PRD: filtro nunca é avaliado — as duas linhas (dentro/fora da janela) chegam."""
    columns = ["fixture_id", "collection_timestamp"]
    dentro = _NOW - timedelta(days=5)
    fora = _NOW - timedelta(days=20)
    rows = [
        {"fixture_id": 1, "collection_timestamp": dentro},
        {"fixture_id": 2, "collection_timestamp": fora},
    ]
    bq = _make_bq(rows, columns)
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy)

    result = mod._sync_one_table(
        bq, conn, "fact_odds_snapshot", tables_ordered=["fact_odds_snapshot"],
        env="prd", sport="futebol", **_FUTEBOL,
    )

    assert result["rows"] == 2
    assert sorted(r[0] for r in fake_copy.rows) == [1, 2]


def test_dev_tabela_sem_regra_nao_filtra_nada():
    """DEV + tabela sem regra configurada (ex.: dim_teams): 100% das linhas passam."""
    columns = ["team_id", "nome"]
    rows = [{"team_id": 1, "nome": "a"}, {"team_id": 2, "nome": "b"}]
    bq = _make_bq(rows, columns)
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy)

    result = mod._sync_one_table(
        bq, conn, "dim_teams", tables_ordered=["dim_teams"],
        env="dev", sport="futebol", **_FUTEBOL,
    )

    assert result["rows"] == 2


# ------------------------------------------------------------------
# 'season' — fact_fixture_player_stats / fact_fixture_lineups_players
# ------------------------------------------------------------------
def test_dev_season_filtra_temporadas_antigas():
    from src.config import FUTEBOL_DEV_CURRENT_SEASON

    columns = ["fixture_id", "season"]
    rows = [
        {"fixture_id": 1, "season": FUTEBOL_DEV_CURRENT_SEASON},
        {"fixture_id": 2, "season": FUTEBOL_DEV_CURRENT_SEASON - 1},
        {"fixture_id": 3, "season": FUTEBOL_DEV_CURRENT_SEASON - 2},
    ]
    bq = _make_bq(rows, columns)
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy)

    result = mod._sync_one_table(
        bq, conn, "fact_fixture_player_stats", tables_ordered=["fact_fixture_player_stats"],
        env="dev", sport="futebol", **_FUTEBOL,
    )

    assert result["rows"] == 1
    assert fake_copy.rows[0][0] == 1


def test_prd_season_nao_filtra_nada():
    from src.config import FUTEBOL_DEV_CURRENT_SEASON

    columns = ["fixture_id", "season"]
    rows = [
        {"fixture_id": 1, "season": FUTEBOL_DEV_CURRENT_SEASON},
        {"fixture_id": 2, "season": FUTEBOL_DEV_CURRENT_SEASON - 1},
    ]
    bq = _make_bq(rows, columns)
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy)

    result = mod._sync_one_table(
        bq, conn, "fact_fixture_lineups_players", tables_ordered=["fact_fixture_lineups_players"],
        env="prd", sport="futebol", **_FUTEBOL,
    )

    assert result["rows"] == 2


# ------------------------------------------------------------------
# 'fixture_window' — int_futebol_odds_devig (lookup cross-table)
# ------------------------------------------------------------------
def test_dev_fixture_window_filtra_por_fixture_id_elegivel():
    columns = ["fixture_id", "line_value"]
    rows = [
        {"fixture_id": 10, "line_value": 1.5},
        {"fixture_id": 20, "line_value": 2.5},
        {"fixture_id": 30, "line_value": 3.5},
    ]
    bq = _make_bq(rows, columns)
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy, eligible_fixture_ids={10, 30})

    result = mod._sync_one_table(
        bq, conn, "int_futebol_odds_devig", tables_ordered=["int_futebol_odds_devig"],
        env="dev", sport="futebol", **_FUTEBOL,
    )

    assert result["rows"] == 2
    assert sorted(r[0] for r in fake_copy.rows) == [10, 30]


def test_dev_fixture_window_lookup_roda_uma_unica_vez():
    """O SELECT contra fact_fixtures roda 1x, não 1x por linha (DE#77, item 9)."""
    columns = ["fixture_id"]
    rows = [{"fixture_id": i} for i in range(50)]
    bq = _make_bq(rows, columns)
    conn = _FakeConn(_FakeCopy(), eligible_fixture_ids=set(range(50)))

    mod._sync_one_table(
        bq, conn, "int_futebol_odds_devig", tables_ordered=["int_futebol_odds_devig"],
        env="dev", sport="futebol", **_FUTEBOL,
    )

    assert len(conn.fixture_lookup_calls) == 1
    # Coluna real de fact_fixtures é `kickoff_utc` (ver dbt_futebol/models/marts/fact_fixtures.sql)
    # — não `kickoff`. Regressão: DE#77 já quebrou uma vez sobre esse nome.
    lookup_sql, _ = conn.fixture_lookup_calls[0]
    assert "kickoff_utc >=" in lookup_sql


def test_prd_fixture_window_nao_faz_lookup_nem_filtra():
    columns = ["fixture_id"]
    rows = [{"fixture_id": 10}, {"fixture_id": 20}]
    bq = _make_bq(rows, columns)
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy)  # eligible_fixture_ids=None: lookup nunca deveria rodar

    result = mod._sync_one_table(
        bq, conn, "int_futebol_odds_devig", tables_ordered=["int_futebol_odds_devig"],
        env="prd", sport="futebol", **_FUTEBOL,
    )

    assert result["rows"] == 2
    assert len(conn.fixture_lookup_calls) == 0


# ------------------------------------------------------------------
# Skip-if-unchanged continua decidindo antes do filtro (DE#75, item 14)
# ------------------------------------------------------------------
def test_dev_skip_if_unchanged_interrompe_antes_do_filtro():
    columns = ["fixture_id", "collection_timestamp"]
    modified = _NOW - timedelta(days=1)
    rows = [{"fixture_id": 1, "collection_timestamp": _NOW - timedelta(days=20)}]
    bq = _make_bq(rows, columns, modified=modified)
    fake_copy = _FakeCopy()
    # last_synced >= modified -> skip, mesmo a linha estando fora da janela.
    conn = _FakeConn(fake_copy, last_synced=modified)

    result = mod._sync_one_table(
        bq, conn, "fact_odds_snapshot", tables_ordered=["fact_odds_snapshot"],
        env="dev", sport="futebol", **_FUTEBOL,
    )

    assert result["skipped"] is True
    assert fake_copy.rows == []


# ------------------------------------------------------------------
# Valor NULL na coluna da regra nunca passa
# ------------------------------------------------------------------
def test_dev_valor_null_na_coluna_da_regra_nunca_passa():
    columns = ["fixture_id", "collection_timestamp"]
    rows = [
        {"fixture_id": 1, "collection_timestamp": None},
        {"fixture_id": 2, "collection_timestamp": _NOW - timedelta(days=1)},
    ]
    bq = _make_bq(rows, columns)
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy)

    result = mod._sync_one_table(
        bq, conn, "fact_odds_snapshot", tables_ordered=["fact_odds_snapshot"],
        env="dev", sport="futebol", **_FUTEBOL,
    )

    assert result["rows"] == 1
    assert fake_copy.rows[0][0] == 2


# ------------------------------------------------------------------
# config.get_dev_retention_rule — resolução isolada (DE#75, "Testing Decisions")
# ------------------------------------------------------------------
def test_get_dev_retention_rule_prd_e_sempre_none():
    from src.config import get_dev_retention_rule

    assert get_dev_retention_rule("futebol", "prd", "fact_odds_snapshot") is None


def test_get_dev_retention_rule_dev_tabela_sem_regra_e_none():
    from src.config import get_dev_retention_rule

    assert get_dev_retention_rule("futebol", "dev", "dim_teams") is None


def test_get_dev_retention_rule_nba_e_sempre_vazio():
    from src.config import get_dev_retention_rule

    assert get_dev_retention_rule("nba", "dev", "ft_games") is None


def test_get_dev_retention_rule_dev_futebol_retorna_regra_esperada():
    from src.config import get_dev_retention_rule

    rule = get_dev_retention_rule("futebol", "dev", "int_futebol_odds_devig")
    assert rule["kind"] == "fixture_window"
    assert rule["requires"] == "fact_fixtures"


# ------------------------------------------------------------------
# Ordem de execução explícita (DE#77): int_futebol_odds_devig sem fact_fixtures
# na mesma run falha, em vez de produzir filtro vazio.
# ------------------------------------------------------------------
def test_run_sync_falha_se_devig_sem_fact_fixtures_na_mesma_run_em_dev():
    with pytest.raises(RuntimeError):
        mod._assert_dev_retention_order(
            "futebol", "dev", ["int_futebol_odds_devig"]
        )


def test_run_sync_ok_se_devig_e_fact_fixtures_na_mesma_run_em_dev():
    mod._assert_dev_retention_order(
        "futebol", "dev", ["fact_fixtures", "int_futebol_odds_devig"]
    )  # não levanta


def test_run_sync_prd_nunca_falha_por_ordem():
    mod._assert_dev_retention_order(
        "futebol", "prd", ["int_futebol_odds_devig"]
    )  # não levanta — regra nunca se aplica em PRD
