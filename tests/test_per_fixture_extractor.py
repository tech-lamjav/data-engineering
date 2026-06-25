"""Testes do loop comum de PerFixtureExtractor (skip-if-exists, vazio, save, sem-fixtures).

Usa FixtureStatisticsExtractor como representante (os 3 compartilham o mesmo loop).
Infra (GCS/API) é mockada — nenhum acesso de rede.
"""
import logging
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def ext():
    # Patches só p/ o __init__ não instanciar GCSStorage()/ApiFootballClient() reais.
    with patch("src.extractors.base_extractor.GCSStorage"), \
         patch("src.extractors.per_fixture_extractor.ApiFootballClient"), \
         patch("src.extractors.per_fixture_extractor.time.sleep", lambda *_: None):
        from src.extractors.fixture_statistics_extractor import FixtureStatisticsExtractor
        e = FixtureStatisticsExtractor(mode="backfill")
        e.storage = MagicMock()
        e.client = MagicMock()
        yield e


def _envelope_com_dados():
    return {"errors": None, "response": [{"team": {"id": 10}, "statistics": [{"type": "Shots", "value": 5}]}]}


def test_salva_um_arquivo_por_fixture(ext):
    ext.storage.get_fixture_ids_from_storage.return_value = [
        {"fixture_id": 1, "date_utc": "2025-01-01"},
        {"fixture_id": 2, "date_utc": "2025-01-02"},
    ]
    ext.storage.bucket.blob.return_value.exists.return_value = False
    ext.storage.upload_json.side_effect = lambda **kw: f"gs://b/{kw['endpoint']}/{kw['game_id']}.json"
    ext.client.get_fixture_statistics.return_value = _envelope_com_dados()

    paths = ext.extract_and_save()

    assert len(paths) == 2
    assert ext.storage.upload_json.call_count == 2
    kw = ext.storage.upload_json.call_args.kwargs
    assert kw["endpoint"] == "fixture_statistics"
    assert kw["sport"] == "futebol"
    assert kw["season"] == 0


def test_skip_if_exists_no_backfill(ext):
    ext.storage.get_fixture_ids_from_storage.return_value = [{"fixture_id": 1, "date_utc": "2025-01-01"}]
    ext.storage.bucket.blob.return_value.exists.return_value = True  # arquivo já existe

    paths = ext.extract_and_save()

    assert paths == []
    ext.client.get_fixture_statistics.assert_not_called()
    ext.storage.upload_json.assert_not_called()


def test_resposta_vazia_nao_salva(ext):
    ext.storage.get_fixture_ids_from_storage.return_value = [{"fixture_id": 1, "date_utc": "2025-01-01"}]
    ext.storage.bucket.blob.return_value.exists.return_value = False
    ext.client.get_fixture_statistics.return_value = {"errors": None, "response": []}

    paths = ext.extract_and_save()

    assert paths == []
    ext.storage.upload_json.assert_not_called()


def test_sem_fixtures_retorna_vazio(ext):
    ext.storage.get_fixture_ids_from_storage.return_value = []

    assert ext.extract_and_save() == []
    ext.storage.upload_json.assert_not_called()


def test_erro_por_fixture_nao_aborta_os_demais(ext):
    ext.storage.get_fixture_ids_from_storage.return_value = [
        {"fixture_id": 1, "date_utc": "2025-01-01"},
        {"fixture_id": 2, "date_utc": "2025-01-02"},
    ]
    ext.storage.bucket.blob.return_value.exists.return_value = False
    # 1º fixture levanta, 2º ok — o loop deve continuar e salvar o 2º.
    ext.client.get_fixture_statistics.side_effect = [RuntimeError("timeout"), _envelope_com_dados()]
    ext.storage.upload_json.side_effect = lambda **kw: f"gs://b/{kw['game_id']}.json"

    paths = ext.extract_and_save()

    assert len(paths) == 1


def test_falha_emite_resumo_error_distinto_de_vazio(ext, caplog):
    """Achado #3: falha por fixture é contabilizada e gera ERROR de RESUMO."""
    ext.storage.get_fixture_ids_from_storage.return_value = [
        {"fixture_id": 1, "date_utc": "2025-01-01"},
        {"fixture_id": 2, "date_utc": "2025-01-02"},
    ]
    ext.storage.bucket.blob.return_value.exists.return_value = False
    ext.client.get_fixture_statistics.side_effect = [RuntimeError("503"), _envelope_com_dados()]
    ext.storage.upload_json.side_effect = lambda **kw: f"gs://b/{kw['game_id']}.json"

    with caplog.at_level(logging.ERROR):
        paths = ext.extract_and_save()

    assert len(paths) == 1
    assert any("RESUMO DE FALHA" in r.message for r in caplog.records if r.levelno == logging.ERROR)


def test_sem_falha_nao_emite_resumo_error(ext, caplog):
    """Vazio (jogo sem dados ainda) NÃO deve gerar ERROR de RESUMO."""
    ext.storage.get_fixture_ids_from_storage.return_value = [
        {"fixture_id": 1, "date_utc": "2025-01-01"},
    ]
    ext.storage.bucket.blob.return_value.exists.return_value = False
    ext.client.get_fixture_statistics.return_value = {"errors": None, "response": []}

    with caplog.at_level(logging.ERROR):
        ext.extract_and_save()

    assert not any("RESUMO DE FALHA" in r.message for r in caplog.records)
