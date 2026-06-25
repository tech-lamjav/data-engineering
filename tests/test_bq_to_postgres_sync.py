"""Testes de _sync_one_table (COPY tipado, streaming, skip-if-unchanged, force).

Mock total de infra (BigQuery client e conexão psycopg) — não toca rede/DB.
Foca no M11 (None vs '' preservados no write_row), no streaming linha-a-linha
(write_row chamado por linha, sem StringIO) e no modo force/full-resync.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

try:
    from src.sync import bq_to_postgres as mod
except Exception as e:  # pragma: no cover
    pytest.skip(f"src.sync.bq_to_postgres não importável: {e}", allow_module_level=True)


def _make_bq(rows, columns, modified):
    """Cria um mock de bigquery.Client cujo list_rows devolve rows/colunas/modified."""
    schema = [MagicMock(name=c) for c in columns]
    for field, name in zip(schema, columns):
        field.name = name

    # Cada "row" do BQ é indexável por nome de coluna (row[c]).
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
    """Captura as linhas escritas via write_row (COPY tipado)."""

    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, row):
        self.rows.append(list(row))


class _FakeCursor:
    def __init__(self, copy_obj, last_synced):
        self._copy = copy_obj
        self._last_synced = last_synced
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        # Usado por _read_last_synced.
        return (self._last_synced,) if self._last_synced is not None else None

    def copy(self, sql):
        self._copy.sql = sql
        return self._copy


class _FakeConn:
    def __init__(self, copy_obj, last_synced):
        self._copy = copy_obj
        self._last_synced = last_synced
        self.committed = False

    def cursor(self):
        return _FakeCursor(self._copy, self._last_synced)

    def commit(self):
        self.committed = True


@pytest.fixture
def table_name():
    return mod.MART_TABLES_ORDERED[0]


def test_sync_preserva_none_e_string_vazia(table_name):
    """M11: None vira NULL, '' permanece string vazia no write_row tipado."""
    columns = ["a", "b", "c"]
    rows = [{"a": None, "b": "", "c": "x"}]
    bq = _make_bq(rows, columns, datetime(2025, 1, 1, tzinfo=timezone.utc))
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy, last_synced=None)

    result = mod._sync_one_table(bq, conn, table_name)

    assert result["rows"] == 1
    assert result["skipped"] is False
    # A linha escrita preserva a distinção: None != ''.
    written = fake_copy.rows[0]
    assert written[0] is None
    assert written[1] == ""
    assert written[2] == "x"
    # COPY tipado: sem 'NULL' textual / sem FORMAT csv na declaração.
    assert "NULL" not in fake_copy.sql
    assert "FORMAT csv" not in fake_copy.sql
    assert conn.committed is True


def test_sync_streaming_uma_chamada_por_linha(table_name):
    """Streaming: write_row é chamado uma vez por linha (sem buffer único)."""
    columns = ["a"]
    rows = [{"a": i} for i in range(5)]
    bq = _make_bq(rows, columns, datetime(2025, 1, 1, tzinfo=timezone.utc))
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy, last_synced=None)

    result = mod._sync_one_table(bq, conn, table_name)

    assert result["rows"] == 5
    assert len(fake_copy.rows) == 5
    assert [r[0] for r in fake_copy.rows] == [0, 1, 2, 3, 4]


def test_skip_if_unchanged(table_name):
    """Skip quando BQ.modified <= last_synced e force=False."""
    columns = ["a"]
    modified = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bq = _make_bq([{"a": 1}], columns, modified)
    fake_copy = _FakeCopy()
    # last_synced igual a modified -> deve pular.
    conn = _FakeConn(fake_copy, last_synced=modified)

    result = mod._sync_one_table(bq, conn, table_name)

    assert result["skipped"] is True
    assert result["rows"] == 0
    assert fake_copy.rows == []  # nada copiado
    assert conn.committed is False  # nenhum commit no skip


def test_force_ignora_skip_if_unchanged(table_name):
    """force=True re-sincroniza mesmo com BQ inalterado (full-resync)."""
    columns = ["a"]
    modified = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bq = _make_bq([{"a": 1}], columns, modified)
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy, last_synced=modified)

    result = mod._sync_one_table(bq, conn, table_name, force=True)

    assert result["skipped"] is False
    assert result["rows"] == 1
    assert len(fake_copy.rows) == 1
    assert conn.committed is True


def test_nao_chama_get_table(table_name):
    """Evita round-trip extra: modified vem de list_rows().table, não get_table."""
    columns = ["a"]
    bq = _make_bq([{"a": 1}], columns, datetime(2025, 1, 1, tzinfo=timezone.utc))
    conn = _FakeConn(_FakeCopy(), last_synced=None)

    mod._sync_one_table(bq, conn, table_name)

    bq.get_table.assert_not_called()
    bq.list_rows.assert_called_once()
