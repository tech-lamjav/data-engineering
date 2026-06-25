"""Testes dos utilitários puros em src/utils/helpers.py."""
from datetime import datetime

import pytest

from src.utils.helpers import (
    parse_date,
    normalize_bigquery_column_name,
    normalize_dict_keys,
    validate_json_structure,
    utcnow_iso,
)


@pytest.mark.parametrize("raw,expected", [
    ("2025-10-21", "2025-10-21"),
    ("2025/10/21", "2025-10-21"),
    ("21/10/2025", "2025-10-21"),
    ("21-10-2025", "2025-10-21"),
])
def test_parse_date_formatos_validos(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("falsy", [None, ""])
def test_parse_date_vazio_retorna_none(falsy):
    assert parse_date(falsy) is None


def test_parse_date_invalida_levanta():
    with pytest.raises(ValueError):
        parse_date("not-a-date")


@pytest.mark.parametrize("raw,expected", [
    ("Points (Q1)", "Points_Q1"),
    ("a--b", "a_b"),
    ("3pt%", "_3pt"),      # caractere inválido vira _, e início numérico ganha _ prefixo
    ("FG%", "FG"),
    ("", "_"),
])
def test_normalize_bigquery_column_name(raw, expected):
    assert normalize_bigquery_column_name(raw) == expected


def test_normalize_dict_keys_recursivo():
    raw = {"Player Name": "x", "stats (adv)": {"3P%": 1}, "lst": [{"a-b": 1}]}
    out = normalize_dict_keys(raw)
    assert "Player_Name" in out
    assert "stats_adv" in out
    assert out["stats_adv"] == {"_3P": 1}
    assert out["lst"] == [{"a_b": 1}]


def test_validate_json_structure():
    assert validate_json_structure({"a": 1, "b": 2}, ["a", "b"]) is True
    assert validate_json_structure({"a": 1}, ["a", "b"]) is False


def test_utcnow_iso_formato_naive():
    s = utcnow_iso()
    # naive: sem offset (+hh:mm) e sem sufixo Z
    assert "+" not in s and not s.endswith("Z")
    # ainda parseável como ISO
    assert isinstance(datetime.fromisoformat(s), datetime)
