"""Testes dos clientes de API (NBA balldontlie + Futebol API-Football).

Cobrem os achados do code review do escopo de clients:
- #4: get_player_props/get_betting_odds (NBA) PAGINAM (cursor/page) e enviam per_page=100,
  em vez de truncar na 1ª página.
- #2: estouro de cota da API-Football (HTTP 200 + errors de rate-limit) vira exceção
  explícita (ApiQuotaExceededError), não "sem dados".
- M1/#13: erro/cota a partir da página 2 de /players e /odds aborta/propaga em vez de
  devolver coleta parcial como completa.

Toda a infra de rede é mockada via _execute_request / _make_request.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.clients.base_client import ApiQuotaExceededError, is_quota_error


def _resp(json_payload):
    """Constrói um objeto tipo requests.Response mockado com .json()."""
    r = MagicMock()
    r.json.return_value = json_payload
    r.status_code = 200
    return r


# --------------------------------------------------------------------------- #
# #4 — Paginação de player_props / betting_odds (NBA)
# --------------------------------------------------------------------------- #
@pytest.fixture
def bdl_client():
    from src.clients.balldontlie_client import BallDontLieClient
    return BallDontLieClient()


def test_player_props_pagina_multiplas_paginas_por_cursor(bdl_client):
    # 2 páginas via next_cursor; a 2ª encerra com cursor vazio.
    pagina1 = {"data": [{"id": 1}, {"id": 2}], "meta": {"next_cursor": "abc"}}
    pagina2 = {"data": [{"id": 3}], "meta": {"next_cursor": None}}

    with patch.object(bdl_client, "_execute_request", side_effect=[_resp(pagina1), _resp(pagina2)]) as m, \
         patch("src.clients.base_client.time.sleep", lambda *_: None):
        props = bdl_client.get_player_props(game_id=99, vendors=["draftkings"])

    assert [p["id"] for p in props] == [1, 2, 3]  # consolidou as 2 páginas
    assert m.call_count == 2
    # per_page=100 deve ser enviado em toda chamada paginada.
    for call in m.call_args_list:
        assert call.kwargs["params"]["per_page"] == 100
    # endpoint e base v2 corretos.
    assert m.call_args_list[0].args[1].endswith("/odds/player_props")


def test_betting_odds_pagina_por_page_total_pages(bdl_client):
    pagina1 = {"data": [{"id": 1}], "meta": {"total_pages": 2, "current_page": 1}}
    pagina2 = {"data": [{"id": 2}], "meta": {"total_pages": 2, "current_page": 2}}

    with patch.object(bdl_client, "_execute_request", side_effect=[_resp(pagina1), _resp(pagina2)]) as m, \
         patch("src.clients.base_client.time.sleep", lambda *_: None):
        odds = bdl_client.get_betting_odds(game_id=42)

    assert [o["id"] for o in odds] == [1, 2]
    assert m.call_count == 2
    assert m.call_args_list[0].kwargs["params"]["per_page"] == 100
    assert m.call_args_list[0].args[1].endswith("/odds")


def test_player_props_resposta_lista_crua(bdl_client):
    # Se a API devolver lista crua (sem envelope), get_paginated extende e encerra.
    with patch.object(bdl_client, "_execute_request", side_effect=[_resp([{"id": 7}])]), \
         patch("src.clients.base_client.time.sleep", lambda *_: None):
        props = bdl_client.get_player_props(game_id=1)
    assert props == [{"id": 7}]


# --------------------------------------------------------------------------- #
# #2 / M1 — Cota e erros por página na API-Football
# --------------------------------------------------------------------------- #
@pytest.fixture
def af_client():
    from src.clients.api_football_client import ApiFootballClient
    return ApiFootballClient()


def test_is_quota_error_detecta_rate_limit():
    assert is_quota_error({"requests": "You have reached the request limit for the day"})
    assert is_quota_error({"rateLimit": "Too many requests"})
    assert is_quota_error(["Quota exceeded"])
    # Erros de parâmetro NÃO são cota.
    assert not is_quota_error({"league": "The League field is required."})
    assert not is_quota_error(None)
    assert not is_quota_error([])
    assert not is_quota_error({})


def test_get_fixtures_quota_levanta_excecao(af_client):
    envelope = {"errors": {"requests": "limit reached"}, "response": []}
    with patch.object(af_client, "_make_request", return_value=_resp(envelope)):
        with pytest.raises(ApiQuotaExceededError):
            af_client.get_fixtures(league_id=71, season=2025)


def test_get_fixtures_erro_de_parametro_nao_levanta(af_client):
    # Erro não-cota é devolvido no envelope (extractor trata), não vira exceção.
    envelope = {"errors": {"league": "required"}, "response": []}
    with patch.object(af_client, "_make_request", return_value=_resp(envelope)):
        out = af_client.get_fixtures(league_id=71, season=2025)
    assert out["errors"] == {"league": "required"}


def test_get_players_quota_na_pagina_2_aborta(af_client):
    # Página 1 ok (paging.total=3); página 2 estoura cota → deve levantar e NÃO devolver parcial.
    p1 = {"errors": [], "paging": {"total": 3}, "response": [{"player": {"id": 1}}]}
    p2 = {"errors": {"requests": "limit reached"}, "paging": {"total": 3}, "response": []}

    with patch.object(af_client, "_make_request", side_effect=[_resp(p1), _resp(p2)]), \
         patch("src.clients.api_football_client.time.sleep", lambda *_: None):
        with pytest.raises(ApiQuotaExceededError):
            af_client.get_players(league_id=71, season=2025)


def test_get_players_erro_nao_cota_na_pagina_2_propaga(af_client):
    p1 = {"errors": [], "paging": {"total": 2}, "response": [{"player": {"id": 1}}]}
    p2 = {"errors": {"token": "invalid"}, "paging": {"total": 2}, "response": []}

    with patch.object(af_client, "_make_request", side_effect=[_resp(p1), _resp(p2)]), \
         patch("src.clients.api_football_client.time.sleep", lambda *_: None):
        with pytest.raises(RuntimeError):
            af_client.get_players(league_id=71, season=2025)


def test_get_players_multipla_pagina_sucesso(af_client):
    p1 = {"errors": [], "paging": {"total": 2}, "response": [{"player": {"id": 1}}]}
    p2 = {"errors": [], "paging": {"total": 2}, "response": [{"player": {"id": 2}}]}

    with patch.object(af_client, "_make_request", side_effect=[_resp(p1), _resp(p2)]), \
         patch("src.clients.api_football_client.time.sleep", lambda *_: None):
        out = af_client.get_players(league_id=71, season=2025)

    assert [r["player"]["id"] for r in out["response"]] == [1, 2]
    assert out["errors"] == []


def test_get_odds_quota_na_pagina_2_aborta(af_client):
    # Forward-only: cota na página 2 não pode virar merge parcial silencioso.
    p1 = {
        "errors": [],
        "paging": {"total": 2},
        "response": [{"league": {}, "fixture": {}, "update": "x", "bookmakers": [{"id": 1}]}],
    }
    p2 = {"errors": {"rateLimit": "exceeded"}, "paging": {"total": 2}, "response": []}

    with patch.object(af_client, "_make_request", side_effect=[_resp(p1), _resp(p2)]), \
         patch("src.clients.api_football_client.time.sleep", lambda *_: None):
        with pytest.raises(ApiQuotaExceededError):
            af_client.get_odds(fixture_id=12345)


def test_get_odds_merge_bookmakers_multipla_pagina(af_client):
    p1 = {
        "errors": [],
        "paging": {"total": 2},
        "response": [{"league": {"id": 71}, "fixture": {"id": 9}, "update": "u", "bookmakers": [{"id": 1}]}],
    }
    p2 = {
        "errors": [],
        "paging": {"total": 2},
        "response": [{"league": {"id": 71}, "fixture": {"id": 9}, "update": "u", "bookmakers": [{"id": 2}]}],
    }

    with patch.object(af_client, "_make_request", side_effect=[_resp(p1), _resp(p2)]), \
         patch("src.clients.api_football_client.time.sleep", lambda *_: None):
        out = af_client.get_odds(fixture_id=9)

    bms = out["response"][0]["bookmakers"]
    assert [b["id"] for b in bms] == [1, 2]  # mergeou as casas das 2 páginas


# --------------------------------------------------------------------------- #
# Baixo — validação de total_count em get_paginated
# --------------------------------------------------------------------------- #
def test_get_paginated_loga_warning_em_divergencia_total_count(bdl_client, caplog):
    import logging
    # total_count=5 mas só vieram 2 itens numa única página (menos que per_page → fim).
    pagina = {"data": [{"id": 1}, {"id": 2}], "meta": {"total_pages": 1, "total_count": 5}}

    with patch.object(bdl_client, "_execute_request", side_effect=[_resp(pagina)]), \
         patch("src.clients.base_client.time.sleep", lambda *_: None), \
         caplog.at_level(logging.WARNING):
        data = bdl_client.get_paginated("games", params={}, per_page=100)

    assert len(data) == 2
    assert any("Divergência de contagem" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# /status — leitura de cota do resumo diário (best-effort, sem o retry longo)
# --------------------------------------------------------------------------- #
def test_get_status_devolve_o_envelope_cru(af_client):
    envelope = {
        "errors": [],
        "response": {
            "subscription": {"plan": "Pro", "end": "2026-08-11T12:21:59+00:00", "active": True},
            "requests": {"current": 4623, "limit_day": 7500},
        },
    }
    af_client.session = MagicMock()
    af_client.session.request.return_value = _resp(envelope)

    assert af_client.get_status() == envelope


def test_get_status_e_uma_tentativa_curta_sem_retry(af_client):
    # A leitura roda ANTES do envio do email, num Cloud Run de timeout 600s. O retry
    # padrão do BaseClient (6 tentativas, até ~810s somando backoff e API_TIMEOUT)
    # derrubaria o email inteiro numa indisponibilidade do /status — o oposto do que a
    # seção de cota existe p/ garantir. Aqui: 1 chamada, timeout curto.
    from src.clients.api_football_client import STATUS_TIMEOUT

    af_client.session = MagicMock()
    af_client.session.request.return_value = _resp({"errors": [], "response": {}})

    af_client.get_status()

    assert af_client.session.request.call_count == 1
    assert af_client.session.request.call_args.kwargs["timeout"] == STATUS_TIMEOUT
    assert STATUS_TIMEOUT <= 60


def test_get_status_propaga_erro_http_para_quem_degrada(af_client):
    # collect_quota transforma isso em seção degradada; o cliente não engole o erro.
    import requests

    af_client.session = MagicMock()
    af_client.session.request.return_value.raise_for_status.side_effect = (
        requests.exceptions.HTTPError("500")
    )

    with pytest.raises(requests.exceptions.HTTPError):
        af_client.get_status()
