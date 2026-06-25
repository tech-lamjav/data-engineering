"""Testes de _format_value do sync BQ->Postgres + documentação do M11 (xfail)."""
from datetime import datetime, date
from decimal import Decimal

import pytest

# Pula o módulo inteiro se as deps pesadas do sync não estiverem instaladas.
try:
    from src.sync.bq_to_postgres import _format_value
except Exception as e:  # pragma: no cover
    pytest.skip(f"src.sync.bq_to_postgres não importável: {e}", allow_module_level=True)


@pytest.mark.parametrize("value,expected", [
    (None, ""),
    (True, "true"),
    (False, "false"),
    (42, "42"),
    ("texto", "texto"),
    (Decimal("1.50"), "1.50"),
    (datetime(2025, 1, 2, 3, 4, 5), "2025-01-02T03:04:05"),
    (date(2025, 1, 2), "2025-01-02"),
])
def test_format_value(value, expected):
    assert _format_value(value) == expected


@pytest.mark.xfail(
    reason="M11: string vazia colide com NULL no COPY (NULL '') — None e '' geram o mesmo token. "
    "Passa quando M11 for corrigido (ex.: None -> '\\N').",
    strict=True,
)
def test_format_value_distingue_none_de_string_vazia():
    # Desejado: None (NULL) e '' (string vazia real) devem ser distinguíveis no CSV,
    # senão ambos viram NULL no Postgres via `NULL ''`.
    assert _format_value(None) != _format_value("")
