"""Testes das funções puras de cadência de placar (DE#60): seleção de candidatos a
refresh e merge/poda do arquivo `_live.json`. Sem GCS, sem API — só dicts em memória.
"""
from datetime import datetime, timedelta, timezone

from src.extractors.fixtures_extractor import (
    filter_tracked_fixtures,
    merge_fixture_rows,
    select_live_candidates,
)

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


def _row(fixture_id, status, hours_ago_kickoff, league_id=71, season=2026):
    ts = int((NOW - timedelta(hours=hours_ago_kickoff)).timestamp())
    return {
        "requested_league_id": league_id,
        "requested_season": season,
        "loaded_at": NOW.isoformat(),
        "fixture": {"id": fixture_id, "status": {"short": status}, "timestamp": ts},
        "league": {"id": league_id, "season": season},
    }


# --------------------------------------------------------------------------- #
# select_live_candidates
# --------------------------------------------------------------------------- #
def test_candidato_com_kickoff_recente_e_status_nao_terminal():
    rows = [_row(1, "1H", hours_ago_kickoff=1)]
    assert select_live_candidates(rows, now=NOW) == [1]


def test_status_terminal_nao_e_candidato():
    rows = [_row(1, "FT", hours_ago_kickoff=1), _row(2, "AET", hours_ago_kickoff=2)]
    assert select_live_candidates(rows, now=NOW) == []


def test_jogo_futuro_nao_e_candidato():
    rows = [_row(1, "NS", hours_ago_kickoff=-2)]  # kickoff daqui a 2h
    assert select_live_candidates(rows, now=NOW) == []


def test_kickoff_fora_da_janela_de_lookback_nao_e_candidato():
    rows = [_row(1, "1H", hours_ago_kickoff=20)]  # default lookback=12h
    assert select_live_candidates(rows, now=NOW, lookback_hours=12) == []


def test_lookback_customizado_e_respeitado():
    rows = [_row(1, "1H", hours_ago_kickoff=20)]
    assert select_live_candidates(rows, now=NOW, lookback_hours=24) == [1]


def test_fixture_sem_id_ou_timestamp_e_ignorado():
    rows = [
        {"fixture": {"status": {"short": "1H"}, "timestamp": None}},
        {"fixture": {"id": 1, "status": {"short": "1H"}}},
    ]
    assert select_live_candidates(rows, now=NOW) == []


def test_sem_linhas_devolve_lista_vazia():
    assert select_live_candidates([], now=NOW) == []


def test_status_explicitamente_null_nao_quebra_a_selecao():
    """Achado do code review: `fixture.status: null` (chave presente, valor None) não
    pode derrubar o ciclo de poll inteiro com AttributeError."""
    rows = [{"fixture": {"id": 1, "status": None, "timestamp": int(NOW.timestamp())}}]
    assert select_live_candidates(rows, now=NOW) == [1]


# --------------------------------------------------------------------------- #
# filter_tracked_fixtures
# --------------------------------------------------------------------------- #
def test_filtra_so_os_ids_rastreados():
    rows = [_row(1, "1H", 1), _row(2, "1H", 1), _row(3, "1H", 1)]
    out = filter_tracked_fixtures(rows, tracked_ids={1, 3})
    assert [r["fixture"]["id"] for r in out] == [1, 3]


def test_nenhum_rastreado_devolve_vazio():
    rows = [_row(1, "1H", 1)]
    assert filter_tracked_fixtures(rows, tracked_ids=set()) == []


# --------------------------------------------------------------------------- #
# merge_fixture_rows
# --------------------------------------------------------------------------- #
def test_fresh_sobrescreve_existing_do_mesmo_fixture_id():
    existing = [_row(1, "1H", hours_ago_kickoff=1)]
    fresh = [_row(1, "FT", hours_ago_kickoff=1)]

    out = merge_fixture_rows(existing, fresh, now=NOW)

    assert len(out) == 1
    assert out[0]["fixture"]["status"]["short"] == "FT"


def test_fixture_so_no_existing_e_mantido():
    existing = [_row(1, "FT", hours_ago_kickoff=2)]
    fresh = [_row(2, "1H", hours_ago_kickoff=1)]

    out = merge_fixture_rows(existing, fresh, now=NOW)

    ids = {r["fixture"]["id"] for r in out}
    assert ids == {1, 2}


def test_retem_ft_do_ciclo_anterior_quando_fixture_sai_dos_candidatos():
    """O achado do design: um jogo que virou FT não pode desaparecer do `_live.json`
    só porque ele deixou de ser candidato no ciclo seguinte."""
    existing = [_row(1, "FT", hours_ago_kickoff=2)]
    fresh = []  # 1 não é mais candidato (terminal); nenhuma chamada nova o traz de volta

    out = merge_fixture_rows(existing, fresh, now=NOW)

    assert len(out) == 1
    assert out[0]["fixture"]["status"]["short"] == "FT"


def test_poda_linha_mais_velha_que_max_age_hours():
    existing = [
        _row(1, "FT", hours_ago_kickoff=1),
        _row(2, "FT", hours_ago_kickoff=100),  # > 72h default
    ]

    out = merge_fixture_rows(existing, [], now=NOW, max_age_hours=72)

    assert [r["fixture"]["id"] for r in out] == [1]


def test_merge_sem_existing_e_sem_fresh_devolve_vazio():
    assert merge_fixture_rows([], [], now=NOW) == []


def test_linha_sem_timestamp_e_podada_nao_mantida_para_sempre():
    """Achado do code review: sem kickoff avaliável, a linha é fail-closed (descartada),
    não retida por padrão — senão o arquivo cresce sem limite com lixo."""
    existing = [{"fixture": {"id": 1, "status": {"short": "FT"}, "timestamp": None}}]
    assert merge_fixture_rows(existing, [], now=NOW) == []
