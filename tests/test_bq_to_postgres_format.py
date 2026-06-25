"""Testes de _format_value do sync BQ->Postgres.

M11 RESOLVIDO: o sync passou a usar o COPY TIPADO do psycopg3
(`copy.write_row`), que serializa tipos e NULL nativamente. _format_value virou
um passthrough que apenas PRESERVA a distinção semântica entre None (NULL) e ''
(string vazia real) — antes ambos colapsavam no mesmo token no CSV com `NULL ''`.
"""
from datetime import datetime, date
from decimal import Decimal

import pytest

# Pula o módulo inteiro se as deps pesadas do sync não estiverem instaladas.
try:
    from src.sync.bq_to_postgres import _format_value
except Exception as e:  # pragma: no cover
    pytest.skip(f"src.sync.bq_to_postgres não importável: {e}", allow_module_level=True)


@pytest.mark.parametrize("value", [
    None,
    True,
    False,
    42,
    3.14,
    "texto",
    "",
    Decimal("1.50"),
    datetime(2025, 1, 2, 3, 4, 5),
    date(2025, 1, 2),
])
def test_format_value_passthrough(value):
    # O COPY tipado recebe o valor nativo; _format_value não deve transformá-lo,
    # apenas repassá-lo para o psycopg3 serializar conforme o tipo da coluna.
    assert _format_value(value) is value


def test_format_value_distingue_none_de_string_vazia():
    # M11: None (NULL) e '' (string vazia real) devem ser distinguíveis para o
    # COPY tipado, senão ambos virariam NULL no Postgres (como ocorria no
    # `NULL ''` do CSV textual). Agora None permanece None e '' permanece ''.
    assert _format_value(None) != _format_value("")
    assert _format_value(None) is None
    assert _format_value("") == ""
