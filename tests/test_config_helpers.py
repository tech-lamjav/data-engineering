"""Testes dos helpers de config centralizados (M12 + baixos da fatia Config+scripts).

Cobre:
- combinations/season_types como fonte única (M12);
- require_env / _int_env (validação opcional de env);
- use_backfill_seasons / get_seasons_to_process (centralização de BACKFILL_SEASONS);
- get_mode (centralização de *_MODE dos extractors de futebol);
- is_http_no_data (dedup do tratamento HTTP-skip de season averages).
"""
import os

import pytest

from src import config
from src.config import (
    SEASON,
    SEASONS,
    SEASON_AVERAGES_COMBINATIONS,
    TEAM_SEASON_AVERAGES_COMBINATIONS,
    SEASON_TYPES,
    require_env,
    _int_env,
    use_backfill_seasons,
    get_seasons_to_process,
    get_mode,
    is_http_no_data,
)


# --------------------------------------------------------------------------- #
# M12 — combinations / season_types como fonte única
# --------------------------------------------------------------------------- #
def test_combinations_sao_listas_de_dicts_com_category_e_type():
    for combo in SEASON_AVERAGES_COMBINATIONS + TEAM_SEASON_AVERAGES_COMBINATIONS:
        assert set(combo.keys()) == {"category", "type"}


def test_season_types_esperados():
    assert SEASON_TYPES == ["regular", "playoffs", "ist"]


def test_scripts_usam_a_fonte_unica_de_combinations():
    # Os scripts de extração devem referenciar as listas de config (não cópias locais).
    import importlib

    sa = importlib.import_module("scripts.extract_season_averages")
    tsa = importlib.import_module("scripts.extract_team_season_averages")
    assert sa.SEASON_AVERAGES_COMBINATIONS is SEASON_AVERAGES_COMBINATIONS
    assert tsa.TEAM_SEASON_AVERAGES_COMBINATIONS is TEAM_SEASON_AVERAGES_COMBINATIONS


# --------------------------------------------------------------------------- #
# require_env / _int_env
# --------------------------------------------------------------------------- #
def test_require_env_retorna_valor(monkeypatch):
    monkeypatch.setenv("FOO_OBRIGATORIA", "bar")
    assert require_env("FOO_OBRIGATORIA") == "bar"


def test_require_env_levanta_quando_ausente(monkeypatch):
    monkeypatch.delenv("FOO_OBRIGATORIA", raising=False)
    with pytest.raises(RuntimeError):
        require_env("FOO_OBRIGATORIA")


def test_require_env_levanta_quando_vazia(monkeypatch):
    monkeypatch.setenv("FOO_OBRIGATORIA", "")
    with pytest.raises(RuntimeError):
        require_env("FOO_OBRIGATORIA")


def test_int_env_usa_default_quando_ausente(monkeypatch):
    monkeypatch.delenv("FOO_INT", raising=False)
    assert _int_env("FOO_INT", 7) == 7


def test_int_env_converte(monkeypatch):
    monkeypatch.setenv("FOO_INT", "42")
    assert _int_env("FOO_INT", 7) == 42


def test_int_env_levanta_em_valor_invalido(monkeypatch):
    monkeypatch.setenv("FOO_INT", "abc")
    with pytest.raises(RuntimeError):
        _int_env("FOO_INT", 7)


# --------------------------------------------------------------------------- #
# BACKFILL_SEASONS
# --------------------------------------------------------------------------- #
def test_use_backfill_seasons_false_por_padrao(monkeypatch):
    monkeypatch.delenv("BACKFILL_SEASONS", raising=False)
    assert use_backfill_seasons() is False
    assert get_seasons_to_process() == [SEASON]


def test_use_backfill_seasons_true(monkeypatch):
    monkeypatch.setenv("BACKFILL_SEASONS", "1")
    assert use_backfill_seasons() is True
    assert get_seasons_to_process() == SEASONS


# --------------------------------------------------------------------------- #
# get_mode
# --------------------------------------------------------------------------- #
def test_get_mode_default_current(monkeypatch):
    monkeypatch.delenv("STANDINGS_MODE", raising=False)
    assert get_mode("STANDINGS_MODE") == "current"


def test_get_mode_le_env(monkeypatch):
    monkeypatch.setenv("STANDINGS_MODE", "backfill")
    assert get_mode("STANDINGS_MODE") == "backfill"


def test_get_mode_default_custom(monkeypatch):
    monkeypatch.delenv("ALGUM_MODE", raising=False)
    assert get_mode("ALGUM_MODE", default="pregame") == "pregame"


# --------------------------------------------------------------------------- #
# is_http_no_data
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeHTTPError(Exception):
    def __init__(self, status_code):
        self.response = _FakeResp(status_code) if status_code is not None else None


@pytest.mark.parametrize("code", [400, 404, 422])
def test_is_http_no_data_true(code):
    assert is_http_no_data(_FakeHTTPError(code)) is True


@pytest.mark.parametrize("code", [429, 500, 503])
def test_is_http_no_data_false_em_erros_reais(code):
    assert is_http_no_data(_FakeHTTPError(code)) is False


def test_is_http_no_data_false_sem_response():
    assert is_http_no_data(_FakeHTTPError(None)) is False
    assert is_http_no_data(Exception("sem response")) is False
