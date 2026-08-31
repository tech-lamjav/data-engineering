"""Testes do modo `live` do FixturesExtractor (DE#60 — cadência da coleta de placar).

Infra (GCS/API) mockada — nenhum acesso de rede. Cobre: chamada incondicional de
`?live=all`, filtro pelos fixture_ids rastreados, chamada condicional de `?ids=` só para
candidatos, merge com o `_live.json` anterior, e erro de API abortando sem sobrescrever.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# Kickoff fixo em relação a "agora" (1h atrás) — dentro da janela de lookback default
# (12h) da seleção de candidatos, independente de quando o teste rodar.
_KICKOFF_TS = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())


@pytest.fixture
def ext():
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.fixtures_extractor.ApiFootballClient"):
        from src.extractors.fixtures_extractor import FixturesExtractor
        e = FixturesExtractor(mode="live")
        e.storage = MagicMock()
        e.client = MagicMock()
        yield e


def _current_row(fixture_id, status="FT"):
    # Default TERMINAL de propósito: testes que não estão testando candidatura a refresh
    # não devem disparar sem querer uma segunda chamada (get_fixtures_by_ids) não mockada.
    return {
        "requested_league_id": 71,
        "requested_season": 2026,
        "loaded_at": "2026-08-31T00:00:00Z",
        "fixture": {"id": fixture_id, "status": {"short": status}, "timestamp": _KICKOFF_TS},
        "league": {"id": 71, "season": 2026},
    }


def _api_item(fixture_id, status="1H"):
    """Item cru da API (sem requested_league_id/loaded_at — quem chama adiciona)."""
    return {
        "fixture": {"id": fixture_id, "status": {"short": status}, "timestamp": _KICKOFF_TS},
        "league": {"id": 71, "season": 2026},
        "teams": {}, "goals": {}, "score": {},
    }


def test_mode_live_nao_valida_contra_current_backfill():
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.fixtures_extractor.ApiFootballClient"):
        from src.extractors.fixtures_extractor import FixturesExtractor
        FixturesExtractor(mode="live")  # não levanta


def test_chama_live_all_incondicionalmente(ext):
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": [_current_row(1)], "live": [],
    }[mode]
    ext.client.get_fixtures_live.return_value = {"errors": None, "response": []}

    ext.extract_and_save()

    ext.client.get_fixtures_live.assert_called_once()


def test_filtra_live_all_pelos_ids_rastreados(ext):
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": [_current_row(1)], "live": [],
    }[mode]
    # live=all devolve fixture 1 (rastreado) e 999 (não é nosso — outra liga do mundo)
    ext.client.get_fixtures_live.return_value = {
        "errors": None,
        "response": [_api_item(1, "1H"), _api_item(999, "1H")],
    }
    ext.client.get_fixtures_by_ids.return_value = {"errors": None, "response": []}
    ext.storage.upload_json.return_value = "gs://b/fixtures/raw_futebol_fixtures_live.json"

    ext.extract_and_save()

    kw = ext.storage.upload_json.call_args.kwargs
    ids_salvos = {r["fixture"]["id"] for r in kw["data"]["fixtures"]}
    assert ids_salvos == {1}
    assert 999 not in ids_salvos


def test_candidato_nao_terminal_dispara_busca_por_ids(ext):
    # fixture 2 no current: kickoff antigo (epoch fixo bem no passado), status ainda NS —
    # candidato a refresh. live=all não devolve nada sobre ele (já saiu do ar).
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": [_current_row(2, status="1H")], "live": [],
    }[mode]
    ext.client.get_fixtures_live.return_value = {"errors": None, "response": []}
    ext.client.get_fixtures_by_ids.return_value = {
        "errors": None, "response": [_api_item(2, "FT")],
    }
    ext.storage.upload_json.return_value = "gs://b/x.json"

    ext.extract_and_save()

    ext.client.get_fixtures_by_ids.assert_called_once_with([2])
    kw = ext.storage.upload_json.call_args.kwargs
    status_salvo = kw["data"]["fixtures"][0]["fixture"]["status"]["short"]
    assert status_salvo == "FT"


def test_sem_candidatos_nao_chama_get_fixtures_by_ids(ext):
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": [_current_row(1, status="FT")], "live": [],
    }[mode]  # já terminal — não é candidato
    ext.client.get_fixtures_live.return_value = {"errors": None, "response": []}
    ext.storage.upload_json.return_value = "gs://b/x.json"

    ext.extract_and_save()

    ext.client.get_fixtures_by_ids.assert_not_called()


def test_merge_com_live_anterior_retem_ft_que_saiu_de_candidato(ext):
    # ciclo anterior já tinha o fixture 3 como FT no _live.json; neste ciclo ele não é mais
    # candidato (terminal) e nenhuma fonte fala dele de novo — tem de sobreviver ao merge.
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": [_current_row(3, status="NS")],  # current ainda não sabe do FT
        "live": [_api_item(3, "FT")],
    }[mode]
    ext.client.get_fixtures_live.return_value = {"errors": None, "response": []}
    ext.storage.upload_json.return_value = "gs://b/x.json"

    ext.extract_and_save()

    kw = ext.storage.upload_json.call_args.kwargs
    rows = kw["data"]["fixtures"]
    assert any(r["fixture"]["id"] == 3 and r["fixture"]["status"]["short"] == "FT" for r in rows)


def test_erro_de_api_no_live_all_aborta_sem_upload(ext):
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": [_current_row(1)], "live": [],
    }[mode]
    ext.client.get_fixtures_live.return_value = {
        "errors": {"requests": "limit reached"}, "response": [],
    }

    with pytest.raises(RuntimeError):
        ext.extract_and_save()

    ext.storage.upload_json.assert_not_called()


def test_erro_de_api_no_by_ids_aborta_sem_upload(ext):
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": [_current_row(2, status="1H")], "live": [],
    }[mode]
    ext.client.get_fixtures_live.return_value = {"errors": None, "response": []}
    ext.client.get_fixtures_by_ids.return_value = {
        "errors": {"requests": "limit reached"}, "response": [],
    }

    with pytest.raises(RuntimeError):
        ext.extract_and_save()

    ext.storage.upload_json.assert_not_called()


def test_mais_de_20_candidatos_fatia_em_lotes_de_ate_20(ext):
    """DE#60 Fase B (confirmado 2026-08-31): a API rejeita lote de ?ids= acima de 20 —
    o extractor precisa fatiar, nunca mandar tudo numa chamada só."""
    rows_25 = [_current_row(i, status="1H") for i in range(1, 26)]  # 25 candidatos
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": rows_25, "live": [],
    }[mode]
    ext.client.get_fixtures_live.return_value = {"errors": None, "response": []}
    ext.client.get_fixtures_by_ids.return_value = {"errors": None, "response": []}
    ext.storage.upload_json.return_value = "gs://b/x.json"

    ext.extract_and_save()

    tamanhos = [len(c.args[0]) for c in ext.client.get_fixtures_by_ids.call_args_list]
    assert ext.client.get_fixtures_by_ids.call_count == 2
    assert tamanhos == [20, 5]
    assert all(t <= 20 for t in tamanhos)


def test_last_fresh_count_reflete_atualizados_no_ciclo_nao_o_total_retido(ext):
    # ciclo anterior: fixture 3 já FT no _live.json (retido pelo merge, não é "fresh" agora).
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": [_current_row(1), _current_row(3)],
        "live": [_api_item(3, "FT")],
    }[mode]
    ext.client.get_fixtures_live.return_value = {"errors": None, "response": [_api_item(1, "1H")]}

    ext.extract_and_save()

    assert ext.last_fresh_count == 1  # só o fixture 1 foi buscado neste ciclo


def test_upload_usa_mode_live(ext):
    ext.storage.get_fixture_rows_from_storage.side_effect = lambda mode: {
        "current": [_current_row(1)], "live": [],
    }[mode]
    ext.client.get_fixtures_live.return_value = {"errors": None, "response": []}
    ext.storage.upload_json.return_value = "gs://b/x.json"

    ext.extract_and_save()

    kw = ext.storage.upload_json.call_args.kwargs
    assert kw["mode"] == "live"
    assert kw["endpoint"] == "fixtures"
    assert kw["sport"] == "futebol"


if __name__ == "__main__":
    import sys
    sys.exit(__import__("subprocess").run(["python3", "-m", "pytest", __file__, "-v"]).returncode)
