"""A carga por troca com fakes (DE#108, ADR 0005): seleção, modo, retentativas, orçamento e a
fiação do `run_sync`. Roda no CI sem Postgres.

O que SÓ um Postgres de verdade prova (lock curto, leitor concorrente, rollback, fidelidade de
dono/ACL/RLS, sombra órfã) está em `tests/test_sync_troca_integracao.py` (pulado por padrão).
Aqui os testes afirmam comportamento observável: que modo cada tabela recebe, quantas vezes a
troca é tentada, o que acontece quando o orçamento acaba, e o que o `run_sync` devolve quando uma
tabela falha. Nenhum afirma a ordem de statements.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import psycopg
import pytest

from src.sync import bq_to_postgres as sync
from src.sync import troca

AGORA = datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------
# Seleção: o que o desenho proíbe é recusado ANTES de tocar em qualquer coisa
# ------------------------------------------------------------------
def test_parse_lista_aceita_csv_lista_e_vazio():
    assert troca.parse_lista("a, b,,c ") == frozenset({"a", "b", "c"})
    assert troca.parse_lista(["a", " b "]) == frozenset({"a", "b"})
    assert troca.parse_lista("") == frozenset()
    assert troca.parse_lista(None) == frozenset()


def test_selecao_vazia_vale_para_qualquer_esporte():
    troca.valida_selecao("nba", frozenset(), frozenset(), ["x"])


def test_nba_nunca_usa_a_troca():
    with pytest.raises(ValueError, match="futebol"):
        troca.valida_selecao("nba", frozenset({"x"}), frozenset(), ["x"])


@pytest.mark.parametrize("campo", ["troca", "staged"])
def test_odds_nao_entram_na_troca_nem_no_staged_ate_a_de109(campo):
    sel = frozenset({"fact_odds_snapshot"})
    args = (sel, frozenset()) if campo == "troca" else (frozenset(), sel)
    with pytest.raises(ValueError, match="DE#109"):
        troca.valida_selecao("futebol", *args, ["fact_odds_snapshot", "fact_fixtures"])


def test_tabela_fora_da_execucao_e_recusada():
    with pytest.raises(ValueError, match="fora desta execução"):
        troca.valida_selecao("futebol", frozenset({"fact_h2h"}), frozenset(), ["fact_fixtures"])


def test_selecao_valida_passa():
    troca.valida_selecao(
        "futebol", frozenset({"fact_fixtures"}), frozenset({"int_futebol_premissas_1x2"}),
        ["fact_fixtures", "int_futebol_premissas_1x2"],
    )


def test_run_sync_recusa_a_selecao_proibida_antes_de_conectar(monkeypatch):
    conectou = []
    monkeypatch.setattr(sync, "get_pg_url", lambda env: "postgresql://fake:5432/db")
    monkeypatch.setattr(sync.psycopg, "connect", lambda *a, **kw: conectou.append(1))
    with pytest.raises(ValueError):
        sync.run_sync(tables="all", env="prd", sport="futebol", troca="fact_odds_snapshot")
    assert conectou == []


# ------------------------------------------------------------------
# Nomes
# ------------------------------------------------------------------
def test_nome_provisorio_cabe_no_limite_e_nao_colide_para_nomes_de_59_ou_mais_caracteres():
    a = "idx_" + "a" * 58
    b = "idx_" + "a" * 57 + "b"  # só o último caractere difere
    pa, pb = troca._provisorio(a), troca._provisorio(b)
    assert len(pa) <= troca.MAX_IDENT and len(pb) <= troca.MAX_IDENT
    assert pa != pb and pa != a


def test_nome_de_tabela_comprido_demais_para_a_sombra_e_recusado():
    with pytest.raises(ValueError):
        troca._confere_nome("t" * 60)
    troca._confere_nome("int_futebol_premissas_1x2")


# ------------------------------------------------------------------
# Modo de carga por tabela
# ------------------------------------------------------------------
def _ctx(**kw):
    return troca.ContextoTroca(
        troca.ConfigTroca(pausa_min_s=0, pausa_max_s=0, **kw.pop("cfg", {})), **kw
    )


def test_sem_contexto_o_modo_e_no_lugar_sem_consultar_o_catalogo(monkeypatch):
    monkeypatch.setattr(troca, "dependentes", lambda *a: pytest.fail("consultou o catálogo"))
    assert troca.escolhe_modo(MagicMock(), "futebol", "fact_fixtures", None) == (troca.MODO_NO_LUGAR, None)


def test_tabela_nao_habilitada_fica_no_lugar_sem_consultar_o_catalogo(monkeypatch):
    monkeypatch.setattr(troca, "dependentes", lambda *a: pytest.fail("consultou o catálogo"))
    ctx = _ctx(troca={"fact_fixtures"})
    assert troca.escolhe_modo(MagicMock(), "futebol", "fact_h2h", ctx) == (troca.MODO_NO_LUGAR, None)


def test_habilitada_sem_dependente_usa_a_troca(monkeypatch):
    monkeypatch.setattr(troca, "dependentes", lambda *a: [])
    ctx = _ctx(troca={"fact_fixtures"})
    assert troca.escolhe_modo(MagicMock(), "futebol", "fact_fixtures", ctx) == (troca.MODO_TROCA, None)


def test_habilitada_com_dependente_cai_no_lugar_com_warning_e_motivo(monkeypatch, caplog):
    monkeypatch.setattr(troca, "dependentes", lambda *a: ["view ou regra de outra relação: vw_premissas_acesas"])
    ctx = _ctx(troca={"int_futebol_premissas_1x2"})
    modo, motivo = troca.escolhe_modo(MagicMock(), "futebol", "int_futebol_premissas_1x2", ctx)
    assert modo == troca.MODO_FALLBACK
    assert "vw_premissas_acesas" in motivo
    assert any("vw_premissas_acesas" in m for m in caplog.messages)


def test_staged_habilitada_usa_staged_mesmo_com_dependente(monkeypatch):
    monkeypatch.setattr(troca, "dependentes", lambda *a: pytest.fail("staged não precisa checar"))
    ctx = _ctx(staged={"int_futebol_premissas_1x2"})
    assert troca.escolhe_modo(MagicMock(), "futebol", "int_futebol_premissas_1x2", ctx) == (
        troca.MODO_STAGED, None
    )


# ------------------------------------------------------------------
# Retentativas e orçamento (relógio e pausa injetados)
# ------------------------------------------------------------------
class _Relogio:
    def __init__(self):
        self.t = 0.0
        self.pausas = []

    def __call__(self):
        return self.t

    def pausa(self, s):
        self.pausas.append(s)
        self.t += s


def _com_relogio(relogio, **cfg):
    return troca.ContextoTroca(
        troca.ConfigTroca(pausa_min_s=10.0, pausa_max_s=10.0, **cfg),
        pausa=relogio.pausa, relogio=relogio, sorteio=lambda a, b: a,
    )


def _tenta_que_falha(relogio, vezes, custo_s=2.0):
    chamadas = []

    def tenta():
        chamadas.append(1)
        if len(chamadas) <= vezes:
            relogio.t += custo_s
            raise psycopg.errors.LockNotAvailable("timeout")
        return 5.0

    return tenta, chamadas


def test_retenta_ate_conseguir_e_devolve_as_tentativas():
    r = _Relogio()
    tenta, chamadas = _tenta_que_falha(r, vezes=2)
    tentativas, ms = troca._com_retentativas(MagicMock(), "t", _com_relogio(r), tenta)
    assert (tentativas, ms) == (3, 5.0)
    assert r.pausas == [10.0, 10.0]


def test_esgota_as_tentativas_e_falha_com_lock_timeout():
    r = _Relogio()
    tenta, chamadas = _tenta_que_falha(r, vezes=99)
    with pytest.raises(troca.TrocaFalhou) as e:
        troca._com_retentativas(MagicMock(), "t", _com_relogio(r, tentativas=4, orcamento_s=10_000), tenta)
    assert e.value.motivo == troca.MOTIVO_LOCK_TIMEOUT and e.value.table == "t"
    assert len(chamadas) == 4
    assert len(r.pausas) == 3  # não há pausa depois da última tentativa


def test_orcamento_de_retentativas_corta_antes_de_esgotar_as_tentativas():
    """Cada falha custa 2 s e cada pausa 10 s: com orçamento de 25 s o corte vem na 3ª falha,
    antes das 8 tentativas, para a execução nunca passar do timeout do serviço."""
    r = _Relogio()
    tenta, chamadas = _tenta_que_falha(r, vezes=99)
    ctx = _com_relogio(r, tentativas=8, orcamento_s=25.0)
    with pytest.raises(troca.TrocaFalhou) as e:
        troca._com_retentativas(MagicMock(), "t", ctx, tenta)
    assert e.value.motivo == troca.MOTIVO_ORCAMENTO_ESGOTADO
    assert len(chamadas) == 3
    assert ctx.orcamento_esgotado()


def test_orcamento_e_compartilhado_entre_as_tabelas_da_execucao():
    r = _Relogio()
    ctx = _com_relogio(r, orcamento_s=25.0)
    tenta, _ = _tenta_que_falha(r, vezes=99)
    with pytest.raises(troca.TrocaFalhou):
        troca._com_retentativas(MagicMock(), "a", ctx, tenta)
    # a próxima tabela, que ainda precisaria da troca, falha na hora (sem carregar nada)
    conn = MagicMock()
    with pytest.raises(troca.TrocaFalhou) as e:
        troca.carrega_por_troca(conn, "futebol", "b", ctx, copiar=lambda *a: pytest.fail("carregou"),
                                atualiza_estado=lambda c: None)
    assert e.value.motivo == troca.MOTIVO_ORCAMENTO_ESGOTADO and e.value.table == "b"
    conn.cursor.assert_not_called()


def test_formato_divergente_nao_e_retentado():
    chamadas = []

    def tenta():
        chamadas.append(1)
        raise troca._FormatoDivergente(["colunas"])

    with pytest.raises(troca.TrocaFalhou) as e:
        troca._com_retentativas(MagicMock(), "t", _ctx(), tenta)
    assert e.value.motivo == troca.MOTIVO_FORMATO_DIVERGENTE
    assert len(chamadas) == 1


def test_dependente_novo_nao_e_retentado():
    chamadas = []

    def tenta():
        chamadas.append(1)
        raise psycopg.errors.DependentObjectsStillExist("view depende")

    with pytest.raises(troca.TrocaFalhou) as e:
        troca._com_retentativas(MagicMock(), "t", _ctx(), tenta)
    assert e.value.motivo == troca.MOTIVO_DEPENDENTE_NOVO
    assert len(chamadas) == 1


def test_erro_inesperado_na_troca_vira_falha_da_tabela_e_reverte():
    conn = MagicMock()

    def tenta():
        raise RuntimeError("segredo: postgresql://user:senha@host/db")

    with pytest.raises(troca.TrocaFalhou) as e:
        troca._com_retentativas(conn, "t", _ctx(), tenta)
    assert e.value.motivo == troca.MOTIVO_ERRO
    assert "senha" not in str(e.value.motivo)  # o corpo HTTP só leva o código do motivo
    conn.rollback.assert_called()


# ------------------------------------------------------------------
# _sync_one_table delega ao modo escolhido (sem TRUNCATE na troca)
# ------------------------------------------------------------------
class _Campo:
    def __init__(self, name):
        self.name, self.mode, self.field_type = name, "NULLABLE", "STRING"


class _Linhas:
    schema = [_Campo("a")]
    table = type("T", (), {"modified": AGORA})()

    def __iter__(self):
        return iter([{"a": "x"}, {"a": "y"}])


class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append(sql)

    def fetchone(self):
        return None  # nunca sincronizada

    def fetchall(self):
        return []

    def copy(self, sql):
        self.conn.log.append(sql)
        return MagicMock(__enter__=lambda s: MagicMock(), __exit__=lambda *a: False)


class _Conn:
    autocommit = False

    def __init__(self):
        self.log = []

    def cursor(self):
        return _Cur(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _bq():
    bq = MagicMock()
    bq.list_rows.return_value = _Linhas()
    return bq


def _um(modo_ctx, conn, monkeypatch, **kw):
    return sync._sync_one_table(
        _bq(), conn, "dim_leagues", "futebol", "futebol", ["dim_leagues"], env="prd",
        sport="futebol", ctx_troca=modo_ctx, **kw,
    )


def test_modo_troca_delega_a_unidade_da_troca_e_nao_trunca(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(troca, "dependentes", lambda *a: [])
    chamadas = []

    def fake(pg_conn, schema, table, ctx, *, copiar, atualiza_estado):
        chamadas.append((schema, table))
        return {"rows": 2, "tentativas": 2, "troca_ms": 7.5, "duracao_s": 1.25}

    monkeypatch.setattr(troca, "carrega_por_troca", fake)
    r = _um(_ctx(troca={"dim_leagues"}), conn, monkeypatch)
    assert chamadas == [("futebol", "dim_leagues")]
    assert not any("TRUNCATE" in str(q) for q in conn.log)
    assert r["modo"] == "troca" and r["rows"] == 2 and r["tentativas"] == 2
    assert r["troca_ms"] == 7.5 and r["duracao_s"] == 1.25 and r["skipped"] is False


def test_modo_staged_delega_ao_caminho_staged(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(
        troca, "carrega_staged",
        lambda *a, **kw: {"rows": 3, "tentativas": 1, "troca_ms": 1.0, "duracao_s": 0.5},
    )
    r = _um(_ctx(staged={"dim_leagues"}), conn, monkeypatch)
    assert r["modo"] == "staged" and r["rows"] == 3


def test_sem_contexto_a_carga_e_a_de_sempre_e_o_modo_e_ecoado(monkeypatch):
    conn = _Conn()
    r = _um(None, conn, monkeypatch)
    assert r["modo"] == "no_lugar" and r["rows"] == 2
    assert any("TRUNCATE" in str(q) for q in conn.log)


def test_fallback_ecoa_o_motivo_e_carrega_no_lugar(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(troca, "dependentes", lambda *a: ["view ou regra de outra relação: vw"])
    r = _um(_ctx(troca={"dim_leagues"}), conn, monkeypatch)
    assert r["modo"] == "no_lugar_fallback" and "vw" in r["fallback_motivo"]
    assert any("TRUNCATE" in str(q) for q in conn.log)


def test_troca_que_falha_propaga_a_excecao_e_nao_devolve_resultado(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(troca, "dependentes", lambda *a: [])

    def fake(*a, **kw):
        raise troca.TrocaFalhou("dim_leagues", troca.MOTIVO_LOCK_TIMEOUT)

    monkeypatch.setattr(troca, "carrega_por_troca", fake)
    with pytest.raises(troca.TrocaFalhou):
        _um(_ctx(troca={"dim_leagues"}), conn, monkeypatch)


# ------------------------------------------------------------------
# run_sync: status parcial, tabelas seguintes e abortos que NÃO viram parcial
# ------------------------------------------------------------------
@pytest.fixture
def run(monkeypatch):
    log: list = []

    def _sync_one(bq, pg_conn, table, *a, ctx_troca=None, **kw):
        log.append(("TABELA", table, ctx_troca))
        if table == "fact_fixtures":
            raise troca.TrocaFalhou(table, troca.MOTIVO_LOCK_TIMEOUT, "8 tentativas")
        return {"table": table, "rows": 1, "skipped": False, "modo": "troca" if ctx_troca else "no_lugar"}

    monkeypatch.setattr(sync, "get_pg_url", lambda env: "postgresql://fake:5432/db")
    monkeypatch.setattr(sync.bigquery, "Client", lambda **kw: MagicMock())
    monkeypatch.setattr(sync.psycopg, "connect", lambda *a, **kw: _Conn())
    monkeypatch.setattr(sync, "check_schema_parity", lambda *a, **kw: [])
    monkeypatch.setattr(sync, "_sync_one_table", _sync_one)
    monkeypatch.setattr(sync, "_ensure_sync_state_table", lambda *a, **kw: None)
    monkeypatch.setattr(sync, "tenta_trava", lambda *a: True)
    monkeypatch.setattr(sync, "solta_trava", lambda *a: None)
    monkeypatch.setattr(troca, "limpa_sombras", lambda *a, **kw: [])
    return log


def test_tabela_que_nao_troca_nao_derruba_as_seguintes_e_o_status_e_parcial(run):
    r = sync.run_sync(
        tables="dim_leagues,fact_fixtures,fact_h2h", env="prd", sport="futebol", troca="fact_fixtures",
    )
    assert r["status"] == "swap_failed"
    assert r["falhas"] == [{"table": "fact_fixtures", "motivo": "lock_timeout"}]
    assert [x["table"] for x in r["synced"]] == ["dim_leagues", "fact_h2h"]
    assert [e[1] for e in run] == ["dim_leagues", "fact_fixtures", "fact_h2h"]  # a 3ª rodou
    assert r["summary"]["falhas_de_troca"] == 1


def test_sem_troca_habilitada_nenhum_contexto_chega_as_tabelas(run):
    sync.run_sync(tables="dim_leagues", env="prd", sport="futebol")
    assert run[0][2] is None


def test_com_troca_habilitada_o_contexto_leva_a_selecao(run):
    sync.run_sync(tables="dim_leagues", env="prd", sport="futebol", troca="dim_leagues", staged="")
    ctx = run[0][2]
    assert ctx.troca == frozenset({"dim_leagues"}) and ctx.staged == frozenset()


def test_parity_e_iam_continuam_abortando_o_sync_inteiro_e_nunca_viram_parcial(run, monkeypatch):
    monkeypatch.setattr(
        sync, "check_schema_parity",
        lambda *a, **kw: [{"table": "dim_leagues", "kind": "type_mismatch", "detail": "x"}],
    )
    r = sync.run_sync(tables="dim_leagues", env="prd", sport="futebol", troca="dim_leagues")
    assert r["status"] == "aborted_schema_drift" and r["synced"] == []
    assert run == []


def test_a_limpeza_de_sombras_roda_depois_da_trava_e_antes_do_parity(monkeypatch, run):
    ordem = []
    monkeypatch.setattr(sync, "tenta_trava", lambda *a: ordem.append("trava") or True)
    monkeypatch.setattr(troca, "limpa_sombras", lambda *a, **kw: ordem.append("limpa") or ["aviso"])
    monkeypatch.setattr(
        sync, "check_schema_parity", lambda *a, **kw: ordem.append("parity") or []
    )
    r = sync.run_sync(tables="dim_leagues", env="prd", sport="futebol")
    assert ordem == ["trava", "limpa", "parity"]
    assert r["avisos"] == ["aviso"]


def test_trava_ocupada_nao_limpa_nada(monkeypatch, run):
    monkeypatch.setattr(sync, "tenta_trava", lambda *a: False)
    monkeypatch.setattr(troca, "limpa_sombras", lambda *a, **kw: pytest.fail("limpou sem a trava"))
    r = sync.run_sync(tables="dim_leagues", env="prd", sport="futebol")
    assert r["status"] == "busy"
