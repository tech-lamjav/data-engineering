"""Testes dos helpers de montagem de URI das external tables (src/bigquery).

Cobre M13: get_external_table_uri / get_player_props_uris derivam o prefixo de
get_gcs_prefix (fonte única, espelha a escrita de get_gcs_path) e o glob de leitura
casa com o caminho de escrita — inclusive o subdiretório q{period} (divergência
corrigida). Não instanciamos o cliente BQ real: usamos __new__ para pular __init__
(que chamaria bigquery.Client()), pois os métodos de URI não tocam self.client.
"""
import pytest

from src.bigquery.bigquery_client import BigQueryClient
from src.config import GCS_BUCKET_NAME, get_gcs_path


@pytest.fixture
def client():
    # Pula __init__ (não cria bigquery.Client): só precisamos dos métodos de path.
    return BigQueryClient.__new__(BigQueryClient)


def test_get_gcs_prefix(client):
    assert (
        client.get_gcs_prefix("games", 2025)
        == f"gs://{GCS_BUCKET_NAME}/nba/games/2025"
    )


def test_prefix_espelha_get_gcs_path():
    # O prefixo de leitura deve ser o diretório onde get_gcs_path grava (escrita).
    write_path = get_gcs_path("games", 2025, date="2025-10-21")  # nba/games/2025/...
    prefix = BigQueryClient.get_gcs_prefix("games", 2025)
    assert write_path.startswith("nba/games/2025/")
    assert prefix == f"gs://{GCS_BUCKET_NAME}/nba/games/2025"


def test_uri_has_date(client):
    assert (
        client.get_external_table_uri("games", 2025, has_date=True)
        == f"gs://{GCS_BUCKET_NAME}/nba/games/2025/*.json"
    )


def test_uri_has_date_cobre_subdir_q_period(client):
    # game_player_stats_period grava em {season}/q{period}/...json (subdir). O glob
    # {season}/*.json com o `*` do BigQuery (casa a barra) cobre esses arquivos.
    write_path = get_gcs_path("game_player_stats_period", 2025, date="2025-10-21", period=1)
    assert "/q1/" in write_path  # confirma o subdir de escrita

    uri = client.get_external_table_uri("game_player_stats_period", 2025, has_date=True)
    assert uri == f"gs://{GCS_BUCKET_NAME}/nba/game_player_stats_period/2025/*.json"
    # O glob é {prefixo}/*.json e o caminho de escrita (com q1/) fica sob o mesmo
    # prefixo de diretório → o `*` do BigQuery (que casa a barra) cobre o subdir.
    glob_dir = uri[: uri.rindex("/") + 1]  # gs://.../game_player_stats_period/2025/
    bare_glob_dir = glob_dir.replace(f"gs://{GCS_BUCKET_NAME}/", "")
    assert write_path.startswith(bare_glob_dir)
    assert write_path[len(bare_glob_dir):].startswith("q1/")


def test_uri_player_props(client):
    assert (
        client.get_external_table_uri("player_props", 2025, has_date=True, market="draftkings")
        == f"gs://{GCS_BUCKET_NAME}/nba/player_props/2025/draftkings/*.json"
    )


def test_uri_season_averages_combo(client):
    assert (
        client.get_external_table_uri(
            "season_averages", 2025, has_date=False, category="general", type="base"
        )
        == f"gs://{GCS_BUCKET_NAME}/nba/season_averages/2025/raw_nba_season_averages_2025-general-base-*.json"
    )


def test_uri_latest_only(client):
    assert (
        client.get_external_table_uri("team_standings", 2025, has_date=False)
        == f"gs://{GCS_BUCKET_NAME}/nba/team_standings/2025/raw_nba_team_standings_2025.json"
    )


def test_get_player_props_uris(client):
    vendors = ["draftkings", "caesars", "betrivers"]
    uris = client.get_player_props_uris(2025, vendors)
    assert uris == [
        f"gs://{GCS_BUCKET_NAME}/nba/player_props/2025/{v}/*.json" for v in vendors
    ]


def test_player_props_uri_consistente_entre_metodos(client):
    # get_player_props_uris e get_external_table_uri devem concordar para o mesmo vendor.
    single = client.get_external_table_uri("player_props", 2025, has_date=True, market="caesars")
    listed = client.get_player_props_uris(2025, ["caesars"])[0]
    assert single == listed


# --------------------------------------------------------------------------- #
# M12: combos importados de src.config (fonte única) — sem definição local
# --------------------------------------------------------------------------- #
def test_combos_vem_do_config():
    import src.bigquery.bigquery_client as bqc
    from src.config import (
        SEASON_AVERAGES_COMBINATIONS,
        TEAM_SEASON_AVERAGES_COMBINATIONS,
    )

    # São exatamente os objetos importados de src.config (mesma referência).
    assert bqc.SEASON_AVERAGES_COMBINATIONS is SEASON_AVERAGES_COMBINATIONS
    assert bqc.TEAM_SEASON_AVERAGES_COMBINATIONS is TEAM_SEASON_AVERAGES_COMBINATIONS


# --------------------------------------------------------------------------- #
# M10: create_external_table faz swap quase-atômico (não deixa a tabela ausente)
# --------------------------------------------------------------------------- #
def _make_client_with_mock():
    import src.bigquery.bigquery_client as bqc

    c = BigQueryClient.__new__(BigQueryClient)
    c.dataset_id = "nba"

    class _MockBQ:
        def __init__(self):
            self.project = "test-project"
            self.created = []
            self.deleted = []

        def delete_table(self, ref):
            self.deleted.append(getattr(ref, "table_id", str(ref)))

        def create_table(self, table):
            tid = table.reference.table_id
            self.created.append(tid)
            return table

    c.client = _MockBQ()
    return c, bqc


def test_create_external_table_cria_temp_antes_de_remover_a_boa():
    # O create da temporária acontece ANTES do delete da tabela real → se o create
    # falhasse, a tabela boa permaneceria. Verificamos a ordem das chamadas.
    c, _ = _make_client_with_mock()

    table = c.create_external_table(
        table_id="raw_games",
        uri="gs://bucket/nba/games/2025/*.json",
        description="x",
    )

    created = c.client.created
    deleted = c.client.deleted
    # Primeiro objeto criado é a temporária; a definitiva é criada depois.
    assert created[0] == "raw_games__tmp_swap"
    assert "raw_games" in created
    # A temporária é criada antes de qualquer delete da tabela real.
    assert created.index("raw_games__tmp_swap") == 0
    # A temporária acaba removida (limpeza no finally).
    assert "raw_games__tmp_swap" in deleted
    # Retorna a tabela definitiva.
    assert table.reference.table_id == "raw_games"


def test_create_external_table_temp_create_falha_nao_remove_a_boa(monkeypatch):
    # Se o create da temporária falhar, a tabela boa NÃO pode ser deletada.
    c, _ = _make_client_with_mock()

    real_create = c.client.create_table

    def flaky_create(table):
        if table.reference.table_id.endswith("__tmp_swap"):
            raise RuntimeError("schema inválido")
        return real_create(table)

    c.client.create_table = flaky_create

    with pytest.raises(RuntimeError):
        c.create_external_table(
            table_id="raw_games",
            uri="gs://bucket/nba/games/2025/*.json",
            description="x",
        )

    # A tabela real "raw_games" nunca foi deletada (só tentativas de limpar a temp).
    assert "raw_games" not in c.client.deleted
