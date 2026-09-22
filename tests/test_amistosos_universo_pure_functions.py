"""Testes das funções puras do universo dos amistosos (DE#93, ADR 0004): recorte por
times já conhecidos, união append-only e guarda de crescimento. Sem GCS, sem API — só
dicts em memória, na mesma forma crua gravada por FixturesExtractor/TeamsExtractor.
"""
from src.extractors.fixtures_extractor import (
    evaluate_known_teams_growth,
    filter_amistosos_universo,
    merge_known_team_ids,
)


def _fixture_row(fixture_id, home_id, away_id):
    return {
        "fixture": {"id": fixture_id},
        "teams": {
            "home": {"id": home_id, "name": f"home-{home_id}"},
            "away": {"id": away_id, "name": f"away-{away_id}"},
        },
    }


# --------------------------------------------------------------------------- #
# filter_amistosos_universo
# --------------------------------------------------------------------------- #
def test_jogo_com_os_dois_times_conhecidos_entra_no_universo():
    rows = [_fixture_row(1, home_id=10, away_id=20)]
    out = filter_amistosos_universo(rows, known_team_ids={10, 20})
    assert [r["fixture"]["id"] for r in out] == [1]


def test_jogo_com_apenas_um_time_conhecido_fica_de_fora():
    rows = [_fixture_row(1, home_id=10, away_id=99)]
    assert filter_amistosos_universo(rows, known_team_ids={10, 20}) == []


def test_jogo_sem_nenhum_time_conhecido_fica_de_fora():
    rows = [_fixture_row(1, home_id=98, away_id=99)]
    assert filter_amistosos_universo(rows, known_team_ids={10, 20}) == []


def test_jogo_com_time_faltando_no_payload_fica_de_fora():
    row = {"fixture": {"id": 1}, "teams": {"home": {"id": 10}, "away": {}}}
    assert filter_amistosos_universo([row], known_team_ids={10, 20}) == []


def test_conjunto_de_conhecidos_vazio_nao_deixa_nada_entrar():
    rows = [_fixture_row(1, home_id=10, away_id=20)]
    assert filter_amistosos_universo(rows, known_team_ids=set()) == []


def test_sem_linhas_devolve_lista_vazia():
    assert filter_amistosos_universo([], known_team_ids={10, 20}) == []


def test_filtra_so_os_jogos_elegiveis_preservando_ordem():
    rows = [
        _fixture_row(1, home_id=10, away_id=20),
        _fixture_row(2, home_id=10, away_id=99),  # um time só
        _fixture_row(3, home_id=20, away_id=30),
    ]
    out = filter_amistosos_universo(rows, known_team_ids={10, 20, 30})
    assert [r["fixture"]["id"] for r in out] == [1, 3]


# --------------------------------------------------------------------------- #
# merge_known_team_ids — a união é append-only
# --------------------------------------------------------------------------- #
def test_uniao_combina_anterior_e_novo():
    assert merge_known_team_ids({10, 20}, {20, 30}) == {10, 20, 30}


def test_time_do_conjunto_anterior_permanece_mesmo_fora_do_novo():
    """O ponto central do append-only: um time que saiu de `new_ids` (ex.: liga removida
    da config) continua no conjunto final."""
    previous = {10, 20, 30}
    new = {20}  # 10 e 30 não aparecem mais nesta execução
    assert merge_known_team_ids(previous, new) == {10, 20, 30}


def test_primeira_execucao_sem_conjunto_anterior_funciona():
    assert merge_known_team_ids(None, {10, 20}) == {10, 20}


def test_primeira_execucao_com_conjunto_anterior_vazio_funciona():
    assert merge_known_team_ids(set(), {10, 20}) == {10, 20}


def test_novo_conjunto_vazio_preserva_o_anterior():
    assert merge_known_team_ids({10, 20}, set()) == {10, 20}


def test_aceita_listas_vindas_de_json_em_vez_de_sets():
    assert merge_known_team_ids([10, 20], [20, 30]) == {10, 20, 30}


# --------------------------------------------------------------------------- #
# evaluate_known_teams_growth
# --------------------------------------------------------------------------- #
def test_crescimento_dentro_do_limite_nao_e_anomalo():
    result = evaluate_known_teams_growth({10, 20}, {10, 20, 30}, max_growth=5)
    assert result["added_count"] == 1
    assert result["added_ids"] == [30]
    assert result["anomalous"] is False
    assert result["is_bootstrap"] is False


def test_crescimento_acima_do_limite_e_anomalo():
    previous = {1, 2}
    merged = previous | set(range(100, 130))  # 30 times novos
    result = evaluate_known_teams_growth(previous, merged, max_growth=20)
    assert result["added_count"] == 30
    assert result["anomalous"] is True


def test_crescimento_exatamente_no_limite_nao_e_anomalo():
    previous = {1}
    merged = previous | set(range(100, 120))  # 20 times novos, max_growth=20
    result = evaluate_known_teams_growth(previous, merged, max_growth=20)
    assert result["added_count"] == 20
    assert result["anomalous"] is False


def test_bootstrap_sem_conjunto_anterior_nao_quebra_e_nao_e_anomalo():
    """Primeira execução: sem baseline para comparar, `anomalous` fica False mesmo com
    centenas de times entrando de uma vez — é o `is_bootstrap` que sinaliza o caso."""
    merged = set(range(1, 301))  # 300 times, bem acima de qualquer max_growth razoável
    result = evaluate_known_teams_growth(None, merged, max_growth=20)
    assert result["is_bootstrap"] is True
    assert result["anomalous"] is False
    assert result["previous_count"] == 0
    assert result["merged_count"] == 300


def test_bootstrap_com_conjunto_anterior_vazio_e_equivalente_a_none():
    result = evaluate_known_teams_growth(set(), {1, 2, 3}, max_growth=1)
    assert result["is_bootstrap"] is True
    assert result["anomalous"] is False


def test_sem_novo_time_added_count_e_zero():
    result = evaluate_known_teams_growth({10, 20}, {10, 20}, max_growth=0)
    assert result["added_count"] == 0
    assert result["anomalous"] is False


def test_time_do_anterior_sumido_do_merged_e_anomalo():
    """Violação do append-only (ADR 0004): se `merged_ids` não vier de
    `merge_known_team_ids`, um time que sumiu é sinalizado, não silenciado."""
    result = evaluate_known_teams_growth({10, 20, 30}, {10, 20}, max_growth=20)
    assert result["removed_ids"] == [30]
    assert result["anomalous"] is True


def test_sem_encolhimento_removed_ids_e_vazio():
    result = evaluate_known_teams_growth({10, 20}, {10, 20, 30}, max_growth=20)
    assert result["removed_ids"] == []


def test_bootstrap_sem_conjunto_anterior_nao_marca_encolhimento_como_anomalo():
    result = evaluate_known_teams_growth(None, {1, 2, 3}, max_growth=0)
    assert result["removed_ids"] == []
    assert result["anomalous"] is False


def test_aceita_listas_vindas_de_json_em_vez_de_sets():
    result = evaluate_known_teams_growth([10, 20], [10, 20, 30], max_growth=5)
    assert result["added_count"] == 1
    assert result["merged_count"] == 3
