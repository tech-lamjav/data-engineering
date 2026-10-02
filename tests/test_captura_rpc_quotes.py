"""Linha de base da RPC `get_futebol_fixture_quotes` ANTES do cache de serving (DE#109).

O critério de pronto da #109 diz que a RPC devolve o mesmo antes e depois, mas depois de ligar o
cache a linha de base não existe mais: ela tem de ser GRAVADA antes. Aqui se prova, sem Postgres,
o que é puro: a captura (amostra por grupo, ids guardados no arquivo), o diff por fixture e linha
(chave = mercado + seleção), o veredito (linha que SOME é falha; ruído esperado é aviso) e que o
modo diff reconsulta os MESMOS ids, sem reamostrar.
"""
import json
from datetime import datetime, timezone

import pytest

from src.monitoring import captura_rpc_quotes as cap

AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

COLUNAS_RPC = [
    "market_key", "market_label", "outcome_label", "outcome_order", "line", "pinnacle_odd",
    "avg_odd", "reference_odd", "best_odd", "best_book", "n_books", "pin_open", "pin_close",
]


def linha(market="btts", sel="Yes", **sobre):
    base = {
        "market_key": market, "market_label": "Both Teams Score", "outcome_label": sel,
        "outcome_order": 1, "line": None, "pinnacle_odd": 1.9, "avg_odd": 1.95,
        "reference_odd": 1.9, "best_odd": 2.05, "best_book": "Bet365", "n_books": 12,
        "pin_open": 1.85, "pin_close": 1.9,
    }
    base.update(sobre)
    return base


class _Cursor:
    def __init__(self, colunas, linhas):
        self.description = [(c,) for c in colunas]
        self._linhas = linhas

    def fetchall(self):
        return self._linhas


class ConexaoFalsa:
    """`execute` devolve a amostra (SQL de fact_fixtures) ou a saída da RPC (por fixture_id)."""

    def __init__(self, amostra, rpc):
        self.amostra, self.rpc = amostra, rpc
        self.read_only = False
        self.consultas = []

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))
        if "get_futebol_fixture_quotes" in sql:
            linhas = self.rpc.get(params[0], [])
            return _Cursor(COLUNAS_RPC, [tuple(l[c] for c in COLUNAS_RPC) for l in linhas])
        return _Cursor(
            ["fixture_id", "kickoff_utc", "grupo"],
            [(i, k, g) for i, k, g in self.amostra],
        )


KICK = datetime(2026, 9, 20, 18, 0)
AMOSTRA = [(1, KICK, "recente"), (2, datetime(2026, 10, 5, 18, 0), "futura"), (3, datetime(2026, 7, 1), "antiga")]
RPC = {1: [linha(), linha(sel="No", outcome_order=2)], 2: [linha()], 3: [linha(pin_open=1.8)]}


def test_captura_guarda_amostra_e_saida_da_rpc_em_json_serializavel():
    arq = cap.captura(ConexaoFalsa(AMOSTRA, RPC), 5, AGORA)
    json.dumps(arq)  # datetime vira texto
    assert arq["capturado_em"].startswith("2026-10-02T12:00")
    assert [f["fixture_id"] for f in arq["fixtures"]] == [1, 2, 3]
    assert [f["grupo"] for f in arq["fixtures"]] == ["recente", "futura", "antiga"]
    assert len(arq["fixtures"][0]["linhas"]) == 2
    assert arq["fixtures"][0]["linhas"][0]["outcome_label"] == "Yes"


def test_a_captura_e_somente_leitura():
    conn = ConexaoFalsa(AMOSTRA, RPC)
    cap.captura(conn, 5, AGORA)
    assert conn.read_only is True


def test_a_sessao_ganha_statement_timeout_antes_de_qualquer_consulta():
    """Carga de odds em curso segura lock na tabela: melhor falhar em 90 s com mensagem do que
    pendurar o wizard."""
    conn = ConexaoFalsa(AMOSTRA, RPC)
    cap.captura(conn, 5, AGORA)
    assert "statement_timeout" in conn.consultas[0][0].lower()


def test_baseline_sem_fixture_recente_aborta_porque_o_diff_seria_vacuamente_verde():
    with pytest.raises(ValueError, match="recente"):
        cap.captura(ConexaoFalsa([(2, KICK, "futura")], RPC), 5, AGORA)


def test_baseline_sem_nenhuma_linha_de_rpc_aborta():
    with pytest.raises(ValueError, match="nenhuma linha"):
        cap.captura(ConexaoFalsa(AMOSTRA, {}), 5, AGORA)


def test_o_diff_reconsulta_os_mesmos_ids_sem_reamostrar():
    antes = cap.captura(ConexaoFalsa(AMOSTRA, RPC), 5, AGORA)
    conn = ConexaoFalsa([(99, KICK, "recente")], RPC)  # uma reamostra daria outro id
    depois = cap.recaptura(conn, antes, AGORA)
    assert [f["fixture_id"] for f in depois["fixtures"]] == [1, 2, 3]
    assert not any("fact_fixtures" in sql for sql, _ in conn.consultas)
    assert conn.read_only is True


def _compara(rpc_depois, antes_rpc=RPC):
    antes = cap.captura(ConexaoFalsa(AMOSTRA, antes_rpc), 5, AGORA)
    depois = cap.recaptura(ConexaoFalsa([], rpc_depois), antes, AGORA)
    return cap.compara(antes, depois)


def test_saida_identica_e_veredito_verde():
    r = _compara(RPC)
    assert r.falhas == [] and r.avisos == []
    assert r.por_grupo["recente"] == {"fixtures": 1, "iguais": 1}


def test_linha_que_some_e_falha_em_qualquer_grupo():
    for fid in (1, 2, 3):
        depois = {**RPC, fid: RPC[fid][:-1] if len(RPC[fid]) > 1 else []}
        r = _compara(depois)
        assert any(f"fixture {fid}" in f and "sumiu" in f for f in r.falhas), (fid, r.falhas)


def test_valor_diferente_em_jogo_ja_disputado_e_falha():
    depois = {**RPC, 1: [linha(best_odd=2.5), RPC[1][1]]}
    r = _compara(depois)
    assert any("fixture 1" in f and "best_odd" in f for f in r.falhas)


def test_valor_diferente_em_jogo_futuro_e_aviso_porque_a_coleta_continua():
    depois = {**RPC, 2: [linha(avg_odd=2.2)]}
    r = _compara(depois)
    assert r.falhas == []
    assert any("fixture 2" in a and "avg_odd" in a for a in r.avisos)


def test_jogo_antigo_perde_a_abertura_da_pinnacle_e_isso_e_aviso():
    """Depois do corte só o T-15m sobrou: `pin_open` (T-24h) some das fixtures antigas."""
    depois = {**RPC, 3: [linha(pin_open=None)]}
    r = _compara(depois)
    assert r.falhas == []
    assert any("fixture 3" in a and "pin_open" in a for a in r.avisos)


def test_diferenca_de_ponto_flutuante_abaixo_da_tolerancia_nao_conta():
    depois = {**RPC, 1: [linha(avg_odd=1.95 + 1e-12), RPC[1][1]]}
    assert _compara(depois).falhas == []


def test_fixture_que_passou_a_devolver_vazio_e_falha_de_linhas_sumidas():
    r = _compara({**RPC, 1: []})
    assert any("fixture 1" in f and "sumiu" in f for f in r.falhas)


def test_linha_nova_em_jogo_ja_disputado_e_falha():
    depois = {**RPC, 1: [*RPC[1], linha(sel="Extra")]}
    r = _compara(depois)
    assert any("fixture 1" in f and "nova" in f for f in r.falhas)


def test_executa_grava_o_arquivo_na_captura_e_le_no_diff(tmp_path):
    caminho = tmp_path / "base.json"
    codigo, texto = cap.executa("captura", str(caminho), ConexaoFalsa(AMOSTRA, RPC), 5, AGORA)
    assert codigo == 0 and caminho.exists()
    assert "3 fixtures" in texto
    codigo, texto = cap.executa("diff", str(caminho), ConexaoFalsa([], RPC), 5, AGORA)
    assert codigo == 0 and "VERDE" in texto
    codigo, texto = cap.executa("diff", str(caminho), ConexaoFalsa([], {**RPC, 1: []}), 5, AGORA)
    assert codigo == 1 and "VERMELHO" in texto


def test_captura_nao_sobrescreve_baseline_existente(tmp_path):
    caminho = tmp_path / "base.json"
    caminho.write_text("{}")
    with pytest.raises(FileExistsError):
        cap.executa("captura", str(caminho), ConexaoFalsa(AMOSTRA, RPC), 5, AGORA)


def test_modo_desconhecido_e_recusado(tmp_path):
    with pytest.raises(ValueError, match="modo"):
        cap.executa("apaga", str(tmp_path / "x.json"), ConexaoFalsa(AMOSTRA, RPC), 5, AGORA)


def test_o_sql_da_amostra_e_so_select_e_deixa_margem_do_corte_de_30_dias():
    sql = cap.SQL_AMOSTRA.lower()
    for proibido in ("insert", "update", "delete", "truncate", "alter", "drop"):
        assert proibido not in sql
    # o grupo "recente" para antes de 30 dias: uma fixture a 29,9 dias sairia do cache no diff
    assert cap.DIAS_MAX_RECENTE < 30


@pytest.fixture
def script():
    import importlib
    import sys

    mod = importlib.import_module("scripts.captura_rpc_quotes")
    for h in mod.logger.handlers:  # o stderr capturado no import fecha junto com o teste
        h.setStream(sys.__stderr__)
    return mod


def test_o_script_exige_o_arquivo_e_devolve_2_em_erro(script):
    assert script.main({"CAPTURA_RPC_MODO": "captura"}, conexao=ConexaoFalsa(AMOSTRA, RPC)) == 2


def test_o_script_grava_e_depois_confere(script, tmp_path, capsys):
    amb = {"CAPTURA_RPC_MODO": "captura", "CAPTURA_RPC_ARQUIVO": str(tmp_path / "b.json")}
    assert script.main(amb, conexao=ConexaoFalsa(AMOSTRA, RPC)) == 0
    amb["CAPTURA_RPC_MODO"] = "diff"
    assert script.main(amb, conexao=ConexaoFalsa([], RPC)) == 0
    assert script.main(amb, conexao=ConexaoFalsa([], {**RPC, 2: []})) == 1
    assert "VERMELHO" in capsys.readouterr().out
