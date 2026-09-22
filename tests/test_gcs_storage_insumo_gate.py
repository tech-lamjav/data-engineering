"""Testes do gate de competição de insumo (ADR 0004, DE#94) em GCSStorage: fixtures de
liga em config.LEAGUES_INSUMO_IDS não entram nos dois leitores que alimentam os fatos
per-fixture pós-jogo (get_fixture_ids_from_storage) nem a escalação pré-jogo
(get_upcoming_fixture_ids). Cliente GCS mockado, nenhum acesso de rede.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from src.config import AMISTOSOS_ID


@pytest.fixture
def storage():
    with patch("src.storage.gcs_storage.storage.Client") as MockClient:
        client = MockClient.return_value
        bucket = MagicMock()
        client.bucket.return_value = bucket

        from src.storage.gcs_storage import GCSStorage
        store = GCSStorage(bucket_name="meu-bucket")
        yield store


def _linha_finalizada(fixture_id, league_id, ts=1750000000):
    return json.dumps({
        "requested_league_id": league_id,
        "requested_season": 2026,
        "fixture": {"id": fixture_id, "status": {"short": "FT"}, "timestamp": ts},
    })


def _linha_ns(fixture_id, league_id, kickoff_ts):
    return json.dumps({
        "requested_league_id": league_id,
        "requested_season": 2026,
        "fixture": {"id": fixture_id, "status": {"short": "NS"}, "timestamp": kickoff_ts},
    })


# --------------------------------------------------------------------------- #
# get_fixture_ids_from_storage — gate dos 4 endpoints per-fixture pós-jogo
# --------------------------------------------------------------------------- #
def test_fixture_de_liga_insumo_nao_entra_nos_finalizados(storage):
    conteudo = "\n".join([
        _linha_finalizada(1, league_id=71),
        _linha_finalizada(2, league_id=AMISTOSOS_ID),
    ])
    storage.bucket.blob.return_value.exists.return_value = True
    storage.bucket.blob.return_value.download_as_text.return_value = conteudo

    fixtures = storage.get_fixture_ids_from_storage("current")

    assert [f["fixture_id"] for f in fixtures] == [1]


def test_sem_liga_insumo_devolve_tudo_como_antes(storage):
    conteudo = "\n".join([_linha_finalizada(1, league_id=71), _linha_finalizada(2, league_id=39)])
    storage.bucket.blob.return_value.exists.return_value = True
    storage.bucket.blob.return_value.download_as_text.return_value = conteudo

    fixtures = storage.get_fixture_ids_from_storage("current")

    assert [f["fixture_id"] for f in fixtures] == [1, 2]


# --------------------------------------------------------------------------- #
# get_upcoming_fixture_ids — gate da escalação pré-jogo (DE#94 estendeu p/ cá)
# --------------------------------------------------------------------------- #
def test_fixture_ns_de_liga_insumo_nao_entra_na_escalacao_pregame(storage):
    now = int(time.time())
    conteudo = "\n".join([
        _linha_ns(1, league_id=71, kickoff_ts=now + 600),
        _linha_ns(2, league_id=AMISTOSOS_ID, kickoff_ts=now + 600),
    ])
    storage.bucket.blob.return_value.exists.return_value = True
    storage.bucket.blob.return_value.download_as_text.return_value = conteudo

    fixtures = storage.get_upcoming_fixture_ids(window_min=45)

    assert [f["fixture_id"] for f in fixtures] == [1]


# --------------------------------------------------------------------------- #
# get_known_team_ids_from_storage / upload_known_team_ids — estado do universo
# --------------------------------------------------------------------------- #
def test_estado_inexistente_devolve_conjunto_vazio(storage):
    storage.bucket.blob.return_value.exists.return_value = False

    assert storage.get_known_team_ids_from_storage() == set()


def test_estado_existente_e_lido_como_set(storage):
    storage.bucket.blob.return_value.exists.return_value = True
    storage.bucket.blob.return_value.download_as_text.return_value = json.dumps(
        {"team_ids": [10, 20, 30]}
    )

    assert storage.get_known_team_ids_from_storage() == {10, 20, 30}


def test_estado_corrompido_vira_conjunto_vazio_sem_levantar(storage):
    storage.bucket.blob.return_value.exists.return_value = True
    storage.bucket.blob.return_value.download_as_text.return_value = "isso nao e json"

    assert storage.get_known_team_ids_from_storage() == set()


def test_upload_known_team_ids_grava_lista_ordenada():
    with patch("src.storage.gcs_storage.storage.Client") as MockClient:
        client = MockClient.return_value
        bucket = MagicMock()
        client.bucket.return_value = bucket

        from src.storage.gcs_storage import GCSStorage
        store = GCSStorage(bucket_name="meu-bucket")

        gcs_path = store.upload_known_team_ids({30, 10, 20})

        assert gcs_path == "gs://meu-bucket/futebol/amistosos_universo/raw_futebol_amistosos_universo.json"
        blob = bucket.blob.return_value
        uploaded = json.loads(blob.upload_from_string.call_args.args[0])
        assert uploaded == {"team_ids": [10, 20, 30]}
