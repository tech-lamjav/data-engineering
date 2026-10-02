"""Cache de serving das odds em PRD (DE#109, ADR 0006): a regra, o filtro e o que ele entrega.

O que é provado aqui, sem BigQuery nem Postgres de verdade:
- a lista de mercados servidos é UMA constante (`src.sync.alvo`), lida pelo sync e pelo contrato;
- a regra de PRD só existe quando o workflow liga a tabela (lançamento escuro), e em DEV o
  mercado servido passa a valer sem ligar nada (história 37);
- o filtro que chega ao BigQuery, EXECUTADO de verdade num SQLite em memória (a cláusula é SQL
  padrão com parâmetros nomeados; o único ajuste é `IN UNNEST(@x)` -> `json_each`): jogo antigo
  só mantém a janela de fechamento (T-15m), jogo recente e futuro mantêm todas as janelas, e
  mercado fora da lista nunca passa;
- que o filtro roda NA QUERY e não em Python depois de ler a tabela inteira.

O que só o BigQuery real prova (bytes faturados, contagens) está no smoke pós-deploy do PR.
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.sync import alvo, odds_serving, retencao

AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------
# UMA constante de mercados servidos
# ------------------------------------------------------------------
def test_mercados_servidos_sao_os_da_decisao_do_victor_de_30_09():
    # 1 Match Winner, 4 Asian Handicap, 5 Goals O/U, 6 Goals O/U 1º tempo (o Victor MANTEVE o 6),
    # 8 Both Teams Score, 12 Double Chance. Saíram 7, 10, 45, 56, 57, 58 e 77.
    assert alvo.MERCADOS_SERVIDOS == (1, 4, 5, 6, 8, 12)


def test_a_constante_e_uma_so_e_o_sync_a_reexporta_sem_copia():
    assert retencao.MERCADOS_SERVIDOS is alvo.MERCADOS_SERVIDOS
    assert odds_serving.MERCADOS_SERVIDOS is alvo.MERCADOS_SERVIDOS


def test_nenhum_modulo_do_sync_ou_do_contrato_redigita_a_lista():
    """Quem precisar da lista importa de `alvo`: uma cópia literal derivaria em silêncio."""
    copia = re.compile(r"\(\s*1\s*,\s*4\s*,\s*5\s*,\s*6\s*,\s*8\s*,\s*12\s*\)")
    for arquivo in [*(REPO / "src" / "sync").glob("*.py"), *(REPO / "src" / "monitoring").glob("*.py")]:
        if arquivo.name == "alvo.py":
            continue
        assert not copia.search(arquivo.read_text(encoding="utf-8")), arquivo.name


def test_todo_mercado_servido_tem_nome_para_o_contrato_conferir_as_rpcs():
    """As RPCs citam `market_name`, não o id: sem o nome a checagem de contrato não enxerga."""
    assert set(alvo.MERCADOS_SERVIDOS_NOMES) == set(alvo.MERCADOS_SERVIDOS)
    assert alvo.MERCADOS_SERVIDOS_NOMES[6] == "Goals Over/Under First Half"


# ------------------------------------------------------------------
# Qual regra cada (ambiente, tabela) recebe
# ------------------------------------------------------------------
ODDS = "fact_odds_snapshot"
LIGADA = frozenset({ODDS})


def test_prd_sem_ligar_a_tabela_nao_tem_regra_lancamento_escuro():
    assert retencao.resolve_regra_retencao("futebol", "prd", ODDS) is None
    assert retencao.resolve_regra_retencao("futebol", "prd", ODDS, cache_serving=frozenset()) is None


def test_prd_com_a_tabela_ligada_recebe_mercados_servidos_30_dias_e_fechamento():
    regra = retencao.resolve_regra_retencao("futebol", "prd", ODDS, cache_serving=LIGADA)

    assert regra["kind"] == "cache_serving"
    assert regra["market_column"] == "market_id"
    assert regra["market_ids"] == alvo.MERCADOS_SERVIDOS
    assert regra["days"] == retencao.RETENCAO_PRODUTO_DIAS_ATRAS == 30
    assert regra["closing_window"] == "t15m"
    assert regra["fixtures_table"] == "fact_fixtures"


def test_ligar_as_odds_nao_cria_regra_para_nenhuma_outra_tabela_de_prd():
    for tabela in ("fact_fixtures", "fact_insumos_medidos", "fact_value_opportunities_hist"):
        assert retencao.resolve_regra_retencao("futebol", "prd", tabela, cache_serving=LIGADA) is None


def test_nba_nunca_recebe_a_regra_mesmo_com_o_nome_igual():
    assert retencao.resolve_regra_retencao("nba", "prd", ODDS, cache_serving=LIGADA) is None


def test_dev_odds_ganha_o_filtro_de_mercados_sem_ligar_nada_e_mantem_a_coleta_de_7_dias():
    regra = retencao.resolve_regra_retencao("futebol", "dev", ODDS)

    assert regra["kind"] == "timestamp_days"
    assert regra["days"] == retencao.RETENCAO_COLETA_DIAS == 7
    assert regra["market_column"] == "market_id"
    assert regra["market_ids"] == alvo.MERCADOS_SERVIDOS


def test_dev_ignora_o_cache_de_serving_a_regra_de_dev_e_sempre_a_propria():
    com = retencao.resolve_regra_retencao("futebol", "dev", ODDS, cache_serving=LIGADA)
    sem = retencao.resolve_regra_retencao("futebol", "dev", ODDS)
    assert com == sem


def test_dev_desfalques_nao_ganha_filtro_de_mercado():
    assert "market_ids" not in retencao.resolve_regra_retencao("futebol", "dev", "fact_injuries_snapshot")


def test_mudar_a_lista_em_tempo_de_execucao_chega_nas_duas_regras(monkeypatch):
    """A constante é lida na CHAMADA do resolvedor: o teste (e uma edição) troca o valor."""
    monkeypatch.setattr(retencao, "MERCADOS_SERVIDOS", (1, 5))

    assert retencao.resolve_regra_retencao("futebol", "dev", ODDS)["market_ids"] == (1, 5)
    assert retencao.resolve_regra_retencao("futebol", "prd", ODDS, cache_serving=LIGADA)["market_ids"] == (1, 5)


# ------------------------------------------------------------------
# O filtro, EXECUTADO (SQLite faz o papel do BigQuery para a cláusula padrão)
# ------------------------------------------------------------------
def _executa_clausula(filtro, linhas):
    """Roda `SELECT ... WHERE <cláusula do filtro>` num SQLite em memória e devolve as linhas.

    A cláusula sai do `FiltroBQ` com parâmetros nomeados (`@x`, que o SQLite também entende).
    Só `IN UNNEST(@lista)` é específico do BigQuery: vira `IN (SELECT value FROM json_each(@lista))`.
    """
    clausula = re.sub(
        r"IN UNNEST\(@(\w+)\)", r"IN (SELECT value FROM json_each(@\1))", filtro.clausula
    )
    params = {}
    for p in filtro.parametros:
        if hasattr(p, "values"):
            params[p.name] = json.dumps(list(p.values))
        elif isinstance(p.value, datetime):
            params[p.name] = p.value.isoformat()
        else:
            params[p.name] = str(p.value) if hasattr(p.value, "isoformat") else p.value
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE odds (linha TEXT, fixture_id INT, market_id INT, collection_window TEXT)"
    )
    conn.executemany("INSERT INTO odds VALUES (?, ?, ?, ?)", linhas)
    return {r[0] for r in conn.execute(f"SELECT linha FROM odds WHERE {clausula}", params)}


def _regra_prd():
    return retencao.resolve_regra_retencao("futebol", "prd", ODDS, cache_serving=LIGADA)


JANELAS = ("daily", "t24h", "t1h", "t15m")
ANTIGA, RECENTE, FUTURA = 100, 200, 300  # fixture_id; só RECENTE e FUTURA estão na lista elegível


def _universo():
    linhas = []
    for fixture in (ANTIGA, RECENTE, FUTURA):
        for janela in JANELAS:
            linhas.append((f"{fixture}/{janela}/1", fixture, 1, janela))
    # mercados fora da lista: nem o fechamento de um jogo antigo os traz de volta
    linhas.append((f"{ANTIGA}/t15m/10", ANTIGA, 10, "t15m"))
    linhas.append((f"{RECENTE}/t24h/45", RECENTE, 45, "t24h"))
    return linhas


def test_jogo_antigo_so_mantem_a_janela_de_fechamento_t15m():
    filtro = odds_serving.filtro_cache_serving(_regra_prd(), [RECENTE, FUTURA])

    passou = _executa_clausula(filtro, _universo())

    antigas = {l for l in passou if l.startswith(f"{ANTIGA}/")}
    assert antigas == {f"{ANTIGA}/t15m/1"}


def test_jogo_recente_e_jogo_futuro_mantem_todas_as_janelas():
    filtro = odds_serving.filtro_cache_serving(_regra_prd(), [RECENTE, FUTURA])

    passou = _executa_clausula(filtro, _universo())

    for fixture in (RECENTE, FUTURA):
        for janela in JANELAS:
            assert f"{fixture}/{janela}/1" in passou


def test_mercado_fora_da_lista_nunca_passa_nem_como_fechamento_de_jogo_antigo():
    filtro = odds_serving.filtro_cache_serving(_regra_prd(), [RECENTE, FUTURA])

    passou = _executa_clausula(filtro, _universo())

    assert f"{ANTIGA}/t15m/10" not in passou
    assert f"{RECENTE}/t24h/45" not in passou


def test_daily_recapturada_continua_correta_o_filtro_nao_depende_de_data_de_captura():
    """`fact_odds_snapshot` NÃO é append-only: a janela daily é recapturada (até 7 vezes por dia)
    e SOBRESCRITA, com `collection_timestamp` e `collection_date` novos. Um filtro por marca-d'água
    ou por corte de partição perderia a linha que mudou de dia; este decide só por mercado,
    fixture elegível e janela, e por isso a linha recapturada continua entrando, uma vez."""
    filtro = odds_serving.filtro_cache_serving(_regra_prd(), [RECENTE, FUTURA])

    assert "collection_timestamp" not in filtro.clausula
    assert "collection_date" not in filtro.clausula
    assert {p.name for p in filtro.parametros} == {"mercados", "ids_elegiveis", "janela_fechamento"}
    # A mesma linha lógica (fixture, mercado, janela daily) em duas capturas diferentes é a MESMA
    # linha depois do dedup do dbt: o filtro entrega a que existe, onde quer que ela esteja.
    assert _executa_clausula(filtro, [("recapturada", FUTURA, 1, "daily")]) == {"recapturada"}


def test_lista_de_elegiveis_vazia_deixa_so_o_fechamento_nunca_tudo():
    filtro = odds_serving.filtro_cache_serving(_regra_prd(), [])

    passou = _executa_clausula(filtro, _universo())

    assert passou == {f"{f}/t15m/1" for f in (ANTIGA, RECENTE, FUTURA)}


# ------------------------------------------------------------------
# O que chega ao BigQuery: dois query jobs, o filtro NA QUERY e não em Python
# ------------------------------------------------------------------
from unittest.mock import MagicMock  # noqa: E402

try:
    from src.sync import bq_to_postgres as mod  # noqa: E402
except Exception as e:  # pragma: no cover
    pytest.skip(f"src.sync.bq_to_postgres não importável: {e}", allow_module_level=True)

FIXTURES_REF = "proj.futebol.fact_fixtures"
COLUNAS_ODDS = [
    "fixture_id", "market_id", "collection_window", "collection_timestamp", "collection_date",
]
TIPOS_ODDS = {
    "fixture_id": "INTEGER", "market_id": "INTEGER",
    "collection_timestamp": "TIMESTAMP", "collection_date": "DATE",
}


def _campo(nome, tipo="STRING"):
    f = MagicMock()
    f.name, f.mode, f.field_type = nome, "NULLABLE", tipo
    return f


def _linhas_bq(dicts):
    out = []
    for d in dicts:
        r = MagicMock()
        r.__getitem__.side_effect = lambda c, _d=d: _d[c]
        out.append(r)
    return out


class BQFalso:
    """BigQuery falso que roteia pela tabela citada na SQL: o job de `fact_fixtures` devolve as
    fixtures elegíveis; o das odds devolve o conjunto JÁ filtrado pelo BigQuery. `list_rows`
    existe e CONTA: o cache de serving nunca lê a tabela inteira por tabledata.list."""

    def __init__(self, fixtures, odds_filtradas, modified=AGORA, num_bytes=700_000_000,
                 colunas=COLUNAS_ODDS):
        self.schema = [_campo(c, TIPOS_ODDS.get(c, "STRING")) for c in colunas]
        self.fixtures = fixtures
        self.odds_filtradas = odds_filtradas
        self.chamadas = []  # (sql, {parametro: valor}, maximum_bytes_billed)
        self.list_rows_chamadas = 0
        self.leituras_da_tabela_inteira = 0

        def _inteira():
            self.leituras_da_tabela_inteira += 1
            return iter(())

        it = MagicMock()
        it.schema = self.schema
        it.table.modified = modified
        it.table.num_bytes = num_bytes
        it.__iter__.side_effect = _inteira
        self._it = it
        self.query = MagicMock(side_effect=self._query)

    def list_rows(self, ref):
        self.list_rows_chamadas += 1
        return self._it

    def _query(self, sql, job_config=None):
        params = {}
        for p in job_config.query_parameters:
            params[p.name] = list(p.values) if hasattr(p, "values") else p.value
        self.chamadas.append((sql, params, job_config.maximum_bytes_billed))
        dados = self.fixtures if "fact_fixtures" in sql else self.odds_filtradas
        resultado = MagicMock()
        resultado.schema = self.schema
        resultado.__iter__.side_effect = lambda: iter(_linhas_bq(dados))
        job = MagicMock()
        job.result.return_value = resultado
        return job


def test_fixtures_elegiveis_chegam_do_bigquery_com_o_corte_de_30_dias_no_kickoff():
    bq = BQFalso(fixtures=[{"fixture_id": 7}, {"fixture_id": 3}, {"fixture_id": 7}], odds_filtradas=[])

    ids = odds_serving.le_fixtures_elegiveis(bq, FIXTURES_REF, _regra_prd(), AGORA, 123)

    assert ids == [3, 7]  # ordenada e sem repetição
    sql, params, teto = bq.chamadas[0]
    assert "`fixture_id`" in sql and "`kickoff_utc` >= @corte_kickoff" in sql
    assert params["corte_kickoff"] == AGORA - timedelta(days=30)
    assert teto == 123


def test_sem_nenhuma_fixture_elegivel_aborta_em_vez_de_gravar_so_o_fechamento():
    bq = BQFalso(fixtures=[], odds_filtradas=[])

    with pytest.raises(RuntimeError, match="nenhuma fixture elegível"):
        odds_serving.le_fixtures_elegiveis(bq, FIXTURES_REF, _regra_prd(), AGORA)


class CopiaFalsa:
    def __init__(self, eventos):
        self.rows, self._eventos = [], eventos

    def __enter__(self):
        self._eventos.append("COPY")
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, row):
        self.rows.append(list(row))


class CursorFalso:
    def __init__(self, conn):
        self._conn, self._ultimo = conn, ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._ultimo = sql
        self._conn.executados.append((sql, params))
        if sql.lstrip().startswith("TRUNCATE"):
            self._conn.eventos.append("TRUNCATE")

    def fetchone(self):
        c = self._conn
        if "information_schema.columns" in self._ultimo:
            return (1,) if c.tem_coluna_versao else None
        if "regra_versao" in self._ultimo and self._ultimo.lstrip().upper().startswith("SELECT"):
            return (c.versao_gravada,) if c.last_synced is not None else None
        if c.last_synced is not None:
            return (c.last_synced,)
        return None

    def copy(self, sql):
        return self._conn.copia


class ConexaoFalsa:
    def __init__(self, last_synced=None, versao_gravada=None, tem_coluna_versao=True):
        self.eventos = []
        self.copia = CopiaFalsa(self.eventos)
        self.executados = []
        self.last_synced = last_synced
        self.versao_gravada = versao_gravada
        self.tem_coluna_versao = tem_coluna_versao
        self.committed = False

    def cursor(self):
        return CursorFalso(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def truncou(self):
        return "TRUNCATE" in self.eventos


def _sync_odds(bq, conn, env="prd", **kw):
    kw.setdefault("cache_serving", LIGADA)
    return mod._sync_one_table(
        bq, conn, ODDS, dataset="futebol", schema="futebol", tables_ordered=[ODDS],
        env=env, sport="futebol", **kw,
    )


ODDS_DO_BQ = [
    {"fixture_id": 200, "market_id": 1, "collection_window": "daily",
     "collection_timestamp": AGORA, "collection_date": AGORA.date()},
    {"fixture_id": 100, "market_id": 1, "collection_window": "t15m",
     "collection_timestamp": AGORA - timedelta(days=90), "collection_date": (AGORA - timedelta(days=90)).date()},
]


def test_prd_com_cache_de_serving_abre_dois_jobs_e_so_grava_o_que_o_bigquery_devolveu():
    bq = BQFalso(fixtures=[{"fixture_id": 200}, {"fixture_id": 300}], odds_filtradas=ODDS_DO_BQ)
    conn = ConexaoFalsa()

    resultado = _sync_odds(bq, conn)

    assert len(bq.chamadas) == 2
    sql_fixtures, _, _ = bq.chamadas[0]
    sql_odds, params, _ = bq.chamadas[1]
    assert "fact_fixtures" in sql_fixtures and "fact_odds_snapshot" in sql_odds
    assert params["mercados"] == list(alvo.MERCADOS_SERVIDOS)
    assert params["ids_elegiveis"] == [200, 300]
    assert params["janela_fechamento"] == "t15m"
    # Nenhum filtro de segunda mão em Python: grava exatamente as 2 linhas que chegaram, inclusive
    # a de um jogo antigo (é o fechamento: o BigQuery a entregou), e nunca lê a tabela inteira.
    assert resultado["rows"] == 2
    assert [r[0] for r in conn.copia.rows] == [200, 100]
    assert bq.leituras_da_tabela_inteira == 0


def test_os_dois_jobs_terminam_antes_do_truncate_e_falha_de_iam_deixa_a_tabela_intacta():
    bq = BQFalso(fixtures=[{"fixture_id": 200}], odds_filtradas=ODDS_DO_BQ)
    bq.query.side_effect = PermissionError("403 sem bigquery.jobs.create")
    conn = ConexaoFalsa()

    with pytest.raises(PermissionError):
        _sync_odds(bq, conn)

    assert not conn.truncou()


def test_teto_de_bytes_do_job_das_odds_e_proporcional_a_tabela_e_o_das_fixtures_e_o_minimo():
    bq = BQFalso(fixtures=[{"fixture_id": 200}], odds_filtradas=[], num_bytes=700_000_000)

    _sync_odds(bq, ConexaoFalsa())

    _, _, teto_fixtures = bq.chamadas[0]
    _, _, teto_odds = bq.chamadas[1]
    assert teto_fixtures == 100 * 1024 * 1024
    assert teto_odds == 1_400_000_000


def test_sem_ligar_a_tabela_prd_continua_lendo_pela_api_gratuita_sem_query_job():
    bq = BQFalso(fixtures=[], odds_filtradas=[])

    _sync_odds(bq, ConexaoFalsa(), cache_serving=frozenset())

    assert bq.chamadas == []
    assert bq.leituras_da_tabela_inteira == 1


def test_ligar_as_odds_nao_abre_job_para_outra_tabela_de_prd():
    bq = BQFalso(fixtures=[], odds_filtradas=[], colunas=["fixture_id"])

    mod._sync_one_table(
        bq, ConexaoFalsa(), "fact_fixtures", dataset="futebol", schema="futebol",
        tables_ordered=["fact_fixtures"], env="prd", sport="futebol", cache_serving=LIGADA,
    )

    assert bq.chamadas == []


def test_dev_odds_filtra_mercados_no_job_unico_sem_precisar_ligar_nada():
    bq = BQFalso(fixtures=[], odds_filtradas=ODDS_DO_BQ)

    _sync_odds(bq, ConexaoFalsa(), env="dev", cache_serving=frozenset())

    assert len(bq.chamadas) == 1
    sql, params, _ = bq.chamadas[0]
    assert "`market_id` IN UNNEST(@mercados)" in sql
    assert params["mercados"] == list(alvo.MERCADOS_SERVIDOS)
    assert "corte" in params  # a coleta de 7 dias continua


# ------------------------------------------------------------------
# Versão da regra: mudar mercados ou o corte força nova carga (história 36)
# ------------------------------------------------------------------
def test_sem_regra_nao_ha_versao():
    assert odds_serving.regra_versao(None) is None


def test_a_versao_descreve_a_regra_em_vigor_de_forma_deterministica():
    v = odds_serving.regra_versao(_regra_prd())

    assert v == odds_serving.regra_versao(_regra_prd())
    assert "mercados=1,4,5,6,8,12" in v and "dias=30" in v and "fechamento=t15m" in v


def test_mudar_a_lista_de_mercados_muda_a_versao(monkeypatch):
    antes = odds_serving.regra_versao(_regra_prd())
    monkeypatch.setattr(retencao, "MERCADOS_SERVIDOS", (1, 4, 5, 6, 8, 12, 45))

    assert odds_serving.regra_versao(_regra_prd()) != antes


def test_mudar_o_corte_de_30_dias_muda_a_versao(monkeypatch):
    antes = odds_serving.regra_versao(_regra_prd())
    monkeypatch.setattr(retencao, "RETENCAO_PRODUTO_DIAS_ATRAS", 45)

    assert odds_serving.regra_versao(_regra_prd()) != antes


def test_prd_e_dev_nao_compartilham_versao_o_rollback_de_prd_e_detectavel():
    dev = retencao.resolve_regra_retencao("futebol", "dev", ODDS)
    assert odds_serving.regra_versao(dev) != odds_serving.regra_versao(_regra_prd())


def _versao_prd():
    return odds_serving.regra_versao(_regra_prd())


def test_bq_inalterado_e_versao_igual_pula_a_tabela_sem_abrir_job():
    bq = BQFalso(fixtures=[{"fixture_id": 1}], odds_filtradas=ODDS_DO_BQ, modified=AGORA)
    conn = ConexaoFalsa(last_synced=AGORA, versao_gravada=_versao_prd())

    resultado = _sync_odds(bq, conn)

    assert resultado["skipped"] is True
    assert bq.chamadas == [] and not conn.truncou()


def test_bq_inalterado_mas_versao_diferente_recarrega_a_tabela(monkeypatch):
    """O `force` não é exposto no serviço: mudar a lista de mercados é o que força a carga."""
    gravada = _versao_prd()
    monkeypatch.setattr(retencao, "MERCADOS_SERVIDOS", (1, 4, 5, 6, 8, 12, 45))
    bq = BQFalso(fixtures=[{"fixture_id": 1}], odds_filtradas=ODDS_DO_BQ, modified=AGORA)
    conn = ConexaoFalsa(last_synced=AGORA, versao_gravada=gravada)

    resultado = _sync_odds(bq, conn)

    assert resultado["skipped"] is False and resultado["rows"] == 2
    assert bq.chamadas[1][1]["mercados"] == [1, 4, 5, 6, 8, 12, 45]


def test_desligar_o_cache_em_prd_recarrega_a_tabela_completa_na_carga_seguinte():
    """Rollback por workflow: o estado guarda a versão do cache; sem regra a versão de agora é
    nenhuma, a tabela completa volta mesmo com o BigQuery inalterado."""
    bq = BQFalso(fixtures=[], odds_filtradas=[], modified=AGORA)
    conn = ConexaoFalsa(last_synced=AGORA, versao_gravada=_versao_prd())

    resultado = _sync_odds(bq, conn, cache_serving=frozenset())

    assert resultado["skipped"] is False
    assert bq.chamadas == [] and bq.leituras_da_tabela_inteira == 1  # tabela inteira, API gratuita


def test_tabela_nunca_carimbada_e_sem_regra_continua_sendo_pulada_como_antes():
    bq = BQFalso(fixtures=[], odds_filtradas=[], modified=AGORA)
    conn = ConexaoFalsa(last_synced=AGORA, versao_gravada=None)

    assert _sync_odds(bq, conn, cache_serving=frozenset())["skipped"] is True


def test_a_carga_grava_a_versao_junto_do_estado_de_sincronizacao():
    bq = BQFalso(fixtures=[{"fixture_id": 1}], odds_filtradas=ODDS_DO_BQ)
    conn = ConexaoFalsa()

    _sync_odds(bq, conn)

    estados = [(sql, p) for sql, p in conn.executados if "_sync_state" in sql and "INSERT" in sql]
    assert len(estados) == 1
    sql, params = estados[0]
    assert "regra_versao" in sql and _versao_prd() in params


def test_sem_a_coluna_no_estado_e_com_regra_ativa_aborta_antes_do_truncate():
    bq = BQFalso(fixtures=[{"fixture_id": 1}], odds_filtradas=ODDS_DO_BQ)
    conn = ConexaoFalsa(tem_coluna_versao=False)

    with pytest.raises(RuntimeError, match="sync_state_regra_versao.sql"):
        _sync_odds(bq, conn)

    assert not conn.truncou() and bq.chamadas == []


def test_sem_a_coluna_e_sem_regra_a_tabela_sincroniza_como_antes_sem_tocar_na_coluna():
    bq = BQFalso(fixtures=[], odds_filtradas=[])
    conn = ConexaoFalsa(tem_coluna_versao=False)

    resultado = _sync_odds(bq, conn, cache_serving=frozenset())

    assert resultado["skipped"] is False
    estados = [sql for sql, _ in conn.executados if "INSERT" in sql and "_sync_state" in sql]
    assert estados and all("regra_versao" not in sql for sql in estados)


def test_tabela_sem_versao_nao_consulta_a_coluna_nem_o_catalogo():
    """Só as odds têm versão de regra: as outras 21 tabelas seguem byte-idênticas."""
    bq = BQFalso(fixtures=[], odds_filtradas=[], colunas=["fixture_id"])
    conn = ConexaoFalsa()

    mod._sync_one_table(
        bq, conn, "fact_fixtures", dataset="futebol", schema="futebol",
        tables_ordered=["fact_fixtures"], env="prd", sport="futebol",
    )

    assert all("regra_versao" not in sql and "information_schema" not in sql for sql, _ in conn.executados)


# ------------------------------------------------------------------
# Seleção: o que o desenho proíbe é recusado ANTES de tocar em qualquer coisa
# ------------------------------------------------------------------
from src.sync import troca  # noqa: E402

_TODAS = [ODDS, "fact_fixtures"]


def _valida(sport="futebol", env="prd", cache=LIGADA, troca_=frozenset(), staged=frozenset(), resolved=None):
    odds_serving.valida_cache_serving(sport, env, cache, troca_, staged, resolved or _TODAS)


def test_selecao_vazia_vale_para_qualquer_esporte_e_ambiente():
    _valida(sport="nba", env="dev", cache=frozenset())


def test_selecao_valida_de_prd_passa():
    _valida()


def test_o_cache_de_serving_so_vale_para_o_futebol():
    with pytest.raises(ValueError, match="futebol"):
        _valida(sport="nba")


def test_dev_nao_liga_o_cache_o_filtro_de_mercados_ja_vale_la_sempre():
    with pytest.raises(ValueError, match="DEV"):
        _valida(env="dev")


def test_so_as_odds_podem_ser_ligadas():
    with pytest.raises(ValueError, match="fact_fixtures"):
        _valida(cache=frozenset({"fact_fixtures"}))


def test_tabela_fora_da_execucao_e_recusada():
    with pytest.raises(ValueError, match="fora desta execução"):
        _valida(resolved=["fact_fixtures"])


def test_as_odds_so_entram_na_troca_de_prd_depois_do_filtro():
    """A sombra completa custaria ~+920 MB: a odds entra na troca DEPOIS do filtro (ADR 0006)."""
    with pytest.raises(ValueError, match="filtro"):
        _valida(cache=frozenset(), troca_=LIGADA)
    with pytest.raises(ValueError, match="filtro"):
        _valida(cache=frozenset(), staged=LIGADA)
    _valida(cache=LIGADA, troca_=LIGADA)  # com o filtro ligado, entra


def test_a_troca_aceita_as_odds_a_lista_de_proibidas_esvaziou_na_de109():
    troca.valida_selecao("futebol", LIGADA, frozenset(), _TODAS)
    assert troca.TABELAS_FORA_DA_TROCA == frozenset()


class _Vistas(list):
    preflights: list


@pytest.fixture
def run(monkeypatch):
    vistas = _Vistas()

    def _sync_one(bq, pg_conn, table, *a, cache_serving=None, **kw):
        vistas.append((table, cache_serving))
        return {"table": table, "rows": 1, "skipped": False, "modo": "no_lugar"}

    preflights = []
    monkeypatch.setattr(mod, "get_pg_url", lambda env: "postgresql://fake:5432/db")
    monkeypatch.setattr(mod.bigquery, "Client", lambda **kw: MagicMock())
    monkeypatch.setattr(mod.psycopg, "connect", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(mod, "check_schema_parity", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "_sync_one_table", _sync_one)
    monkeypatch.setattr(mod, "_ensure_sync_state_table", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "tenta_trava", lambda *a: True)
    monkeypatch.setattr(mod, "solta_trava", lambda *a: None)
    monkeypatch.setattr(mod, "_verifica_query_job", lambda bq: preflights.append(1))
    monkeypatch.setattr(troca, "limpa_sombras", lambda *a, **kw: [])
    vistas.preflights = preflights
    return vistas


def test_run_sync_recusa_a_selecao_invalida_antes_de_conectar(monkeypatch):
    conectou = []
    monkeypatch.setattr(mod, "get_pg_url", lambda env: "postgresql://fake:5432/db")
    monkeypatch.setattr(mod.psycopg, "connect", lambda *a, **kw: conectou.append(1))

    with pytest.raises(ValueError):
        mod.run_sync(tables=ODDS, env="dev", sport="futebol", cache_serving=ODDS)

    assert conectou == []


def test_run_sync_leva_a_selecao_do_cache_a_cada_tabela(run):
    mod.run_sync(tables="all", env="prd", sport="futebol", cache_serving=ODDS)

    assert {t: c for t, c in run}[ODDS] == LIGADA


def test_sem_cache_ligado_a_selecao_chega_vazia(run):
    mod.run_sync(tables=ODDS + ",fact_fixtures", env="prd", sport="futebol")

    assert all(c == frozenset() for _, c in run)


def test_prd_com_o_cache_ligado_prova_o_iam_do_query_job_antes_de_qualquer_carga(run):
    """Sem `bigquery.jobs.create` o sync inteiro aborta antes do TRUNCATE, como o parity check."""
    mod.run_sync(tables="all", env="prd", sport="futebol", cache_serving=ODDS)

    assert run.preflights == [1]


def test_prd_sem_o_cache_ligado_nao_exige_o_iam_de_query_job(run):
    mod.run_sync(tables="all", env="prd", sport="futebol")

    assert run.preflights == []


def test_sem_a_coluna_o_sync_inteiro_aborta_antes_de_carregar_qualquer_tabela(run, monkeypatch):
    """A coluna `regra_versao` faltando é erro de deploy (SQL administrativo esquecido): aborta o
    sync INTEIRO antes de qualquer carga, não só quando a execução chega nas odds (as tabelas
    anteriores já teriam sido recarregadas)."""
    monkeypatch.setattr(mod, "_tem_coluna_regra_versao", lambda conn, schema: False)

    with pytest.raises(RuntimeError, match="sync_state_regra_versao.sql"):
        mod.run_sync(tables="all", env="prd", sport="futebol", cache_serving=ODDS)

    assert list(run) == []  # nenhuma tabela foi carregada


def test_sem_a_coluna_e_sem_regra_ativa_o_sync_segue_como_antes(run, monkeypatch):
    monkeypatch.setattr(mod, "_tem_coluna_regra_versao", lambda conn, schema: False)

    mod.run_sync(tables="all", env="prd", sport="futebol")  # PRD, cache desligado

    assert len(run) > 0


def test_em_dev_a_regra_de_mercados_tambem_exige_a_coluna(run, monkeypatch):
    monkeypatch.setattr(mod, "_tem_coluna_regra_versao", lambda conn, schema: False)

    with pytest.raises(RuntimeError, match="sync_state_regra_versao.sql"):
        mod.run_sync(tables="all", env="dev", sport="futebol")

    assert list(run) == []
