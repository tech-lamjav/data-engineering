"""Resolução das regras de retenção de DEV do sync (DE#106).

O resolvedor mora em `src/sync/retencao.py`, NÃO em `src/config.py`: o `config.py` entra no
carimbo de procedência dos 29 serviços (ADR 0001) e editá-lo deriva a frota inteira. Os
números esperados abaixo vêm da spec (DE#106/DE#112), escritos como literais de propósito:
um teste que importasse a constante para comparar com ela mesma não falharia nunca.
"""
import pytest

from src.sync import retencao

PRODUTO = [
    "fact_insumos_medidos",
    "int_futebol_premissas_1x2",
    "int_futebol_premissas_ou",
    "int_futebol_premissas_ah",
    "int_futebol_premissas_btts",
    "int_futebol_premissas_dc",
    "fact_value_opportunities_hist",
]
COLETA = ["fact_odds_snapshot", "fact_injuries_snapshot"]


@pytest.mark.parametrize("tabela", PRODUTO + COLETA + ["fact_fixture_player_stats"])
def test_prd_nunca_tem_regra(tabela):
    assert retencao.resolve_regra_retencao("futebol", "prd", tabela) is None


@pytest.mark.parametrize("tabela", PRODUTO)
def test_dev_tabela_de_produto_corta_30_atras_e_14_a_frente_por_kickoff(tabela):
    regra = retencao.resolve_regra_retencao("futebol", "dev", tabela)

    assert regra["kind"] == "fixture_window"
    assert regra["column"] == "fixture_id"
    assert regra["days"] == 30
    assert regra["days_ahead"] == 14
    assert regra["requires"] == "fact_fixtures"


@pytest.mark.parametrize("tabela", COLETA)
def test_dev_tabela_de_coleta_cai_de_14_para_7_dias(tabela):
    regra = retencao.resolve_regra_retencao("futebol", "dev", tabela)

    assert regra["kind"] == "timestamp_days"
    assert regra["days"] == 7


def test_dev_odds_corta_pela_captura_e_aponta_a_coluna_de_particao():
    regra = retencao.resolve_regra_retencao("futebol", "dev", "fact_odds_snapshot")

    assert regra["column"] == "collection_timestamp"
    assert regra["partition_column"] == "collection_date"


def test_dev_desfalques_cortam_por_snapshot_date():
    regra = retencao.resolve_regra_retencao("futebol", "dev", "fact_injuries_snapshot")

    assert regra["column"] == "snapshot_date"


def test_retencao_de_coleta_e_de_produto_sao_constantes_separadas(monkeypatch):
    """Mudar uma não mexe na outra (história 5)."""
    monkeypatch.setattr(retencao, "RETENCAO_COLETA_DIAS", 3)

    coleta = retencao.resolve_regra_retencao("futebol", "dev", "fact_odds_snapshot")
    produto = retencao.resolve_regra_retencao("futebol", "dev", "fact_insumos_medidos")

    assert coleta["days"] == 3
    assert (produto["days"], produto["days_ahead"]) == (30, 14)

    monkeypatch.setattr(retencao, "RETENCAO_PRODUTO_DIAS_ATRAS", 40)
    monkeypatch.setattr(retencao, "RETENCAO_PRODUTO_DIAS_A_FRENTE", 21)
    produto = retencao.resolve_regra_retencao("futebol", "dev", "fact_insumos_medidos")

    assert (produto["days"], produto["days_ahead"]) == (40, 21)
    assert retencao.resolve_regra_retencao("futebol", "dev", "fact_odds_snapshot")["days"] == 3


def test_dev_devig_nao_tem_regra_porque_saiu_do_sync():
    assert retencao.resolve_regra_retencao("futebol", "dev", "int_futebol_odds_devig") is None


def test_dev_regras_por_temporada_continuam_as_do_config():
    from src.config import FUTEBOL_DEV_CURRENT_SEASON

    for tabela in ("fact_fixture_player_stats", "fact_fixture_lineups_players"):
        regra = retencao.resolve_regra_retencao("futebol", "dev", tabela)
        assert regra["kind"] == "season"
        assert regra["season"] == FUTEBOL_DEV_CURRENT_SEASON


@pytest.mark.parametrize(
    "tabela", ["dim_teams", "fact_fixtures", "fact_value_opportunities", "fact_h2h"]
)
def test_dev_tabelas_sem_corte_seguem_sem_regra(tabela):
    assert retencao.resolve_regra_retencao("futebol", "dev", tabela) is None


def test_nba_nunca_tem_regra():
    assert retencao.resolve_regra_retencao("nba", "dev", "ft_games") is None


def test_regras_de_produto_dependem_de_tabela_do_alvo_do_sync():
    """`requires` aponta para tabela que o sync de fato copia (senão a checagem de ordem
    exigiria uma tabela que nunca está na execução)."""
    from src.sync.alvo import resolve_alvo_sync

    _, _, alvo = resolve_alvo_sync("futebol")
    for tabela in PRODUTO + COLETA:
        regra = retencao.resolve_regra_retencao("futebol", "dev", tabela)
        assert tabela in alvo
        assert regra.get("requires", "fact_fixtures") in alvo


def test_sql_do_job_12_versionado_so_limpa_o_cron_e_nao_apaga_tabela_de_futebol():
    """O SQL é aplicado à mão no DEV (sem teste automático da execução; o pg_cron só existe lá),
    mas o que está versionado não pode voltar a apagar tabelas que o sync já poda."""
    from pathlib import Path

    sql = Path(__file__).resolve().parent.parent / "scripts" / "sql" / "job12_purge_so_job_run_details.sql"
    codigo = "\n".join(
        linha for linha in sql.read_text(encoding="utf-8").splitlines()
        if not linha.lstrip().startswith("--")
    )

    assert "cron.alter_job" in codigo and "job_id  => 12" in codigo
    assert "cron.job_run_details" in codigo
    assert "futebol." not in codigo


def test_mercados_servidos_e_uma_constante_so_e_inclui_o_6_por_decisao_do_victor():
    """Decisão de 30/09 (PPP#542): 1, 4, 5, 8, 12 mais o 6 (Gols O/U no 1º tempo)."""
    assert retencao.MERCADOS_SERVIDOS == (1, 4, 5, 6, 8, 12)
    for removido in (7, 10, 45, 56, 57, 58, 77):
        assert removido not in retencao.MERCADOS_SERVIDOS


def test_a_retencao_de_dev_desta_fatia_nao_aplica_filtro_de_mercado():
    """O filtro de mercados é da #109. Aplicá-lo aqui mudaria o que o staging mostra de odds
    sem a pergunta do Victor ter sido toda respondida (o volume do 6 ainda está com ele)."""
    regra = retencao.resolve_regra_retencao("futebol", "dev", "fact_odds_snapshot")

    assert not any("market" in str(chave) for chave in regra)
