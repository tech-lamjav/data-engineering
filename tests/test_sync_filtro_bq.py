"""Filtro no BigQuery, reutilizável pelo sync de DEV (DE#106) e de PRD (DE#109).

O filtro em si (o SQL) só se prova contra o BigQuery real: dry-run e contagens, coladas no PR.
Aqui se prova o que é observável sem ele: o que o módulo manda ao BigQuery (um job, com os
parâmetros certos e o teto de bytes), o que devolve (as linhas do job, sem tocar nelas) e que
a composição de filtros nunca fabrica um parâmetro sem referência nem uma referência sem
parâmetro. Nada aqui afirma o texto da query.
"""
import re
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.sync.filtro_bq import FiltroBQ, le_tabela_filtrada

AGORA = datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)


def _bq_falso(linhas):
    resultado = MagicMock(name="RowIterator")
    resultado.__iter__.return_value = iter(linhas)
    job = MagicMock(name="QueryJob")
    job.result.return_value = resultado
    bq = MagicMock(name="bq")
    bq.query.return_value = job
    return bq, resultado


def _params(bq):
    cfg = bq.query.call_args.kwargs["job_config"]
    return {p.name: p for p in cfg.query_parameters}


def _sql(bq):
    return bq.query.call_args.args[0]


def test_le_tabela_filtrada_devolve_o_resultado_do_job_sem_tocar_nas_linhas():
    bq, resultado = _bq_falso([])
    filtro = FiltroBQ.desde("collection_timestamp", AGORA, "corte")

    leitura = le_tabela_filtrada(bq, "proj.futebol.fact_odds_snapshot", ["fixture_id"], filtro)

    assert leitura is resultado
    assert bq.query.call_count == 1


def test_o_job_leva_o_corte_como_parametro_tipado_e_nao_como_texto():
    bq, _ = _bq_falso([])
    filtro = FiltroBQ.desde("collection_timestamp", AGORA, "corte")

    le_tabela_filtrada(bq, "proj.futebol.t", ["a"], filtro)

    p = _params(bq)["corte"]
    assert (p.type_, p.value) == ("TIMESTAMP", AGORA)
    assert AGORA.isoformat() not in _sql(bq)


def test_data_vira_parametro_date_e_lista_de_ids_vira_array_int64():
    bq, _ = _bq_falso([])
    filtro = FiltroBQ.desde("snapshot_date", date(2026, 9, 24), "corte").e(
        FiltroBQ.em_lista("fixture_id", [10, 30], "ids")
    )

    le_tabela_filtrada(bq, "proj.futebol.t", ["a"], filtro)

    params = _params(bq)
    assert (params["corte"].type_, params["corte"].value) == ("DATE", date(2026, 9, 24))
    assert (params["ids"].array_type, list(params["ids"].values)) == ("INT64", [10, 30])


def test_lista_vazia_continua_sendo_array_tipado_e_nao_quebra_o_job():
    bq, _ = _bq_falso([])

    le_tabela_filtrada(bq, "proj.futebol.t", ["a"], FiltroBQ.em_lista("fixture_id", [], "ids"))

    assert _params(bq)["ids"].array_type == "INT64"
    assert list(_params(bq)["ids"].values) == []


def test_teto_de_bytes_faturados_vai_no_job_quando_pedido():
    bq, _ = _bq_falso([])

    le_tabela_filtrada(
        bq, "proj.futebol.t", ["a"], FiltroBQ.igual("season", 2026, "temporada"),
        maximo_bytes_faturados=123_456,
    )

    assert bq.query.call_args.kwargs["job_config"].maximum_bytes_billed == 123_456


def test_sem_teto_pedido_o_job_nao_inventa_um():
    bq, _ = _bq_falso([])

    le_tabela_filtrada(bq, "proj.futebol.t", ["a"], FiltroBQ.igual("season", 2026, "temporada"))

    assert bq.query.call_args.kwargs["job_config"].maximum_bytes_billed is None


def test_so_as_colunas_pedidas_sao_lidas():
    bq, _ = _bq_falso([])

    le_tabela_filtrada(
        bq, "proj.futebol.t", ["fixture_id", "valor"], FiltroBQ.igual("season", 2026, "temporada")
    )

    sql = _sql(bq)
    assert "`fixture_id`" in sql and "`valor`" in sql
    assert "SELECT *" not in sql.upper()


@pytest.mark.parametrize("combina", ["e", "ou"])
def test_composicao_mantem_todos_os_parametros_e_cada_referencia_tem_parametro(combina):
    a = FiltroBQ.desde("collection_timestamp", AGORA, "corte")
    b = FiltroBQ.desde("collection_date", date(2026, 9, 23), "corte_particao")
    c = FiltroBQ.em_lista("fixture_id", [1], "ids")

    filtro = getattr(a, combina)(b).e(c)

    nomes = {p.name for p in filtro.parametros}
    referencias = set(re.findall(r"@(\w+)", filtro.clausula))
    assert nomes == {"corte", "corte_particao", "ids"}
    assert referencias == nomes


def test_composicao_com_nome_de_parametro_repetido_falha_alto():
    a = FiltroBQ.igual("season", 2026, "p")
    b = FiltroBQ.igual("league_id", 71, "p")

    with pytest.raises(ValueError, match="p"):
        a.e(b)
    with pytest.raises(ValueError, match="p"):
        a.ou(b)


def test_instante_sem_fuso_e_recusado_em_vez_de_deslocar_o_corte_em_silencio():
    with pytest.raises(ValueError):
        FiltroBQ.desde("collection_timestamp", datetime(2026, 10, 1, 13, 0), "corte")


def test_identificador_com_crase_ou_fora_do_padrao_e_recusado():
    """Coluna e tabela entram no SQL como identificador, nunca como parâmetro."""
    with pytest.raises(ValueError):
        FiltroBQ.igual("season`; DROP TABLE x; --", 2026, "temporada")
    with pytest.raises(ValueError):
        le_tabela_filtrada(
            MagicMock(), "proj.futebol.t`; --", ["a"], FiltroBQ.igual("season", 2026, "t")
        )
    with pytest.raises(ValueError):
        le_tabela_filtrada(
            MagicMock(), "proj.futebol.t", ["a b"], FiltroBQ.igual("season", 2026, "t")
        )
