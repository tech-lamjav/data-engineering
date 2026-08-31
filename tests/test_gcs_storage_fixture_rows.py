"""Testes de `GCSStorage.get_fixture_rows_from_storage` (DE#60).

Leitor genérico de NDJSON de fixtures, sem filtro — base para a seleção de candidatos e o
merge do `_live.json` (funções puras em src/extractors/fixtures_extractor.py). Cliente GCS
mockado, nenhum acesso de rede.
"""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def storage():
    with patch("src.storage.gcs_storage.storage.Client") as MockClient:
        client = MockClient.return_value
        bucket = MagicMock()
        client.bucket.return_value = bucket

        from src.storage.gcs_storage import GCSStorage
        store = GCSStorage(bucket_name="meu-bucket")
        yield store


def _linha(fixture_id, status="NS"):
    return json.dumps({
        "requested_league_id": 71,
        "requested_season": 2026,
        "loaded_at": "2026-08-31T00:00:00Z",
        "fixture": {"id": fixture_id, "status": {"short": status}, "timestamp": 1750000000},
        "league": {"id": 71, "season": 2026},
    })


def test_arquivo_inexistente_devolve_lista_vazia(storage):
    storage.bucket.blob.return_value.exists.return_value = False

    assert storage.get_fixture_rows_from_storage("live") == []


def test_le_todas_as_linhas_sem_filtrar_por_status(storage):
    conteudo = "\n".join([_linha(1, "NS"), _linha(2, "FT"), _linha(3, "1H")])
    storage.bucket.blob.return_value.exists.return_value = True
    storage.bucket.blob.return_value.download_as_text.return_value = conteudo

    rows = storage.get_fixture_rows_from_storage("current")

    assert len(rows) == 3
    assert [r["fixture"]["id"] for r in rows] == [1, 2, 3]


def test_linha_invalida_e_pulada_sem_quebrar_as_demais(storage):
    conteudo = "\n".join([_linha(1), "{isso nao e json valido", _linha(2)])
    storage.bucket.blob.return_value.exists.return_value = True
    storage.bucket.blob.return_value.download_as_text.return_value = conteudo

    rows = storage.get_fixture_rows_from_storage("current")

    assert [r["fixture"]["id"] for r in rows] == [1, 2]


def test_le_o_arquivo_do_mode_pedido(storage):
    storage.bucket.blob.return_value.exists.return_value = True
    storage.bucket.blob.return_value.download_as_text.return_value = _linha(1)

    storage.get_fixture_rows_from_storage("live")

    storage.bucket.blob.assert_called_with("futebol/fixtures/raw_futebol_fixtures_live.json")
