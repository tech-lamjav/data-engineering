"""Testes de _sync_one_table (COPY tipado, streaming, skip-if-unchanged, force).

Mock total de infra (BigQuery client e conexão psycopg) — não toca rede/DB.
Foca no M11 (None vs '' preservados no write_row), no streaming linha-a-linha
(write_row chamado por linha, sem StringIO), no modo force/full-resync e no
skip de colunas complexas (REPEATED/RECORD) introduzido p/ o sync de futebol.

_sync_one_table agora é sport-agnostic: recebe (dataset, schema, tables_ordered)
explícitos. Os testes usam o alvo NBA (dataset 'nba', schema 'nba_mart').
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

try:
    from src.sync import bq_to_postgres as mod
except Exception as e:  # pragma: no cover
    pytest.skip(f"src.sync.bq_to_postgres não importável: {e}", allow_module_level=True)


def _field(name, mode="NULLABLE", field_type="STRING"):
    """SchemaField mockado com name/mode/field_type concretos (p/ _is_complex_field)."""
    f = MagicMock()
    f.name = name
    f.mode = mode
    f.field_type = field_type
    return f


def _make_bq(rows, columns, modified):
    """Cria um mock de bigquery.Client cujo list_rows devolve rows/colunas/modified."""
    schema = [_field(c) for c in columns]

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


# Alvo NBA resolvido — _sync_one_table recebe dataset/schema/tables_ordered explícitos.
_NBA = dict(dataset="nba", schema="nba_mart")


@pytest.fixture
def table_name():
    from src.config import MART_TABLES_ORDERED
    return MART_TABLES_ORDERED[0]


def _sync(bq, conn, table_name, **kw):
    """Helper: chama _sync_one_table com o alvo NBA e a allowlist = [table_name]."""
    return mod._sync_one_table(
        bq, conn, table_name, tables_ordered=[table_name], **_NBA, **kw
    )


def test_sync_preserva_none_e_string_vazia(table_name):
    """M11: None vira NULL, '' permanece string vazia no write_row tipado."""
    columns = ["a", "b", "c"]
    rows = [{"a": None, "b": "", "c": "x"}]
    bq = _make_bq(rows, columns, datetime(2025, 1, 1, tzinfo=timezone.utc))
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy, last_synced=None)

    result = _sync(bq, conn, table_name)

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

    result = _sync(bq, conn, table_name)

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

    result = _sync(bq, conn, table_name)

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

    result = _sync(bq, conn, table_name, force=True)

    assert result["skipped"] is False
    assert result["rows"] == 1
    assert len(fake_copy.rows) == 1
    assert conn.committed is True


def test_nao_chama_get_table(table_name):
    """Evita round-trip extra: modified vem de list_rows().table, não get_table."""
    columns = ["a"]
    bq = _make_bq([{"a": 1}], columns, datetime(2025, 1, 1, tzinfo=timezone.utc))
    conn = _FakeConn(_FakeCopy(), last_synced=None)

    _sync(bq, conn, table_name)

    bq.get_table.assert_not_called()
    bq.list_rows.assert_called_once()


def test_pula_colunas_complexas_repeated_record(table_name):
    """Colunas REPEATED (array) e RECORD (struct) são puladas do COPY (futebol).

    Espelha dim_leagues.coverage (RECORD) e evidencias/avisos (ARRAY<STRING>): só
    as escalares entram no column_list e no write_row; o Postgres nativo é escalar.
    """
    schema = [
        _field("fixture_id", field_type="INTEGER"),
        _field("nome", field_type="STRING"),
        _field("evidencias", mode="REPEATED", field_type="STRING"),
        _field("coverage", field_type="RECORD"),
    ]
    row = {"fixture_id": 7, "nome": "x", "evidencias": ["a"], "coverage": {"k": 1}}
    rm = MagicMock()
    rm.__getitem__.side_effect = lambda c: row[c]
    row_iter = MagicMock()
    row_iter.schema = schema
    row_iter.table.modified = datetime(2025, 1, 1, tzinfo=timezone.utc)
    row_iter.__iter__.return_value = iter([rm])
    bq = MagicMock()
    bq.list_rows.return_value = row_iter
    fake_copy = _FakeCopy()
    conn = _FakeConn(fake_copy, last_synced=None)

    result = _sync(bq, conn, table_name)

    assert result["rows"] == 1
    # só as 2 escalares no COPY; evidencias/coverage NÃO aparecem
    assert '"fixture_id"' in fake_copy.sql
    assert '"nome"' in fake_copy.sql
    assert "evidencias" not in fake_copy.sql
    assert "coverage" not in fake_copy.sql
    # write_row recebe só os valores escalares, na ordem
    assert fake_copy.rows[0] == [7, "x"]


# --- DE#110: backpressure do COPY (flush periódico do buffer de saída da libpq) ---
#
# Sintoma: com o servidor ingerindo mais devagar que o BQ entrega, o RSS do sync sobe
# até o tamanho do backlog (~220 B/linha; 4,2 mi linhas de fact_odds_snapshot ≈ 900 MiB),
# porque no Linux o psycopg (PREFER_FLUSH só no macOS) apenas enfileira no buffer da
# libpq, que cresce sem limite em modo não bloqueante. Estes testes travam o MECANISMO
# do fix (flush a cada N linhas, esperando o socket); o sintoma em si (RSS) só é visível
# com Postgres real + servidor lento — harness em scripts/repro_sync_copy_memoria/.


class _FakePgConn:
    """pgconn mínimo: flush() devolve 1 (pendente) `pending` vezes antes de 0."""

    def __init__(self, events, pending=0):
        self._events = events
        self._pending = pending
        self.socket = 42
        self.consumed = 0

    def flush(self):
        self._events.append("flush")
        if self._pending > 0:
            self._pending -= 1
            return 1
        return 0

    def consume_input(self):
        self.consumed += 1


class _EventCopy(_FakeCopy):
    def __init__(self, events):
        super().__init__()
        self._events = events

    def write_row(self, row):
        self._events.append("row")
        super().write_row(row)


def _conn_com_pgconn(copy_obj, pgconn):
    conn = _FakeConn(copy_obj, last_synced=None)
    conn.pgconn = pgconn
    return conn


def test_copy_faz_flush_periodico_dentro_do_copy(table_name, monkeypatch):
    """A cada COPY_FLUSH_EVERY_ROWS linhas escritas há um flush, intercalado com o write_row."""
    monkeypatch.setattr(mod, "COPY_FLUSH_EVERY_ROWS", 3)
    events = []
    bq = _make_bq(
        [{"a": i} for i in range(7)], ["a"], datetime(2025, 1, 1, tzinfo=timezone.utc)
    )
    conn = _conn_com_pgconn(_EventCopy(events), _FakePgConn(events))

    result = _sync(bq, conn, table_name)

    assert result["rows"] == 7
    assert events == [
        "row", "row", "row", "flush",
        "row", "row", "row", "flush",
        "row",
    ]


def test_flush_espera_o_socket_enquanto_houver_pendencia(monkeypatch):
    """flush()==1 (servidor lento) bloqueia no select até a libpq esvaziar — é o backpressure."""
    events = []
    pgconn = _FakePgConn(events, pending=2)
    waits = []

    def fake_select(r, w, x, timeout=None):
        waits.append((r, w, timeout))
        return ([], [pgconn.socket], [])  # só gravável: nada a consumir

    monkeypatch.setattr(mod.select, "select", fake_select)
    conn = MagicMock()
    conn.pgconn = pgconn

    mod._flush_copy_buffer(conn)

    assert events == ["flush", "flush", "flush"]  # 2 pendentes + o que zerou
    assert len(waits) == 2
    # espera bloqueante (timeout None, senão vira busy-spin) e observa leitura+escrita
    assert all(r == [pgconn.socket] and w == [pgconn.socket] and t is None for r, w, t in waits)
    assert pgconn.consumed == 0


def test_flush_consome_input_quando_socket_legivel(monkeypatch):
    """Doc do PQflush (modo não bloqueante): read-ready => consume_input antes de novo flush."""
    events = []
    pgconn = _FakePgConn(events, pending=1)
    monkeypatch.setattr(
        mod.select, "select", lambda r, w, x, timeout=None: ([pgconn.socket], [], [])
    )
    conn = MagicMock()
    conn.pgconn = pgconn

    mod._flush_copy_buffer(conn)

    assert pgconn.consumed == 1
    assert events == ["flush", "flush"]
