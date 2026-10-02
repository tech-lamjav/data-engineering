"""A lista de mercados servidos conferida contra as RPCs vivas do PRD (DE#109, história 38).

O cache de serving corta de `fact_odds_snapshot` em PRD tudo o que está fora de
`alvo.MERCADOS_SERVIDOS`. Se o app passar a ler outro mercado numa RPC (o Victor escreve o
`market_name` na função), a tela viraria "sem cotação" sem alarme. A checagem roda no workflow do
contrato de serving (leitura só, semanal): lê o corpo das funções vivas que citam a tabela de
odds e falha se alguma citar um mercado fora da lista. As RPCs citam o NOME do mercado
(`market_name = 'Match Winner'`), não o id, por isso a lista tem os nomes.
"""
import importlib

import pytest

from src.monitoring import contrato_serving
from src.sync import alvo

TABELA = "fact_odds_snapshot"
SCHEMA = "futebol"

# Corpo no formato real da `get_futebol_fixture_quotes` (migration 107 do app), reduzido.
RPC_QUOTES = """
create function public.get_futebol_fixture_quotes(p bigint) returns table(m text) as $$
  select o.market_name from futebol.fact_odds_snapshot o
  where o.fixture_id = p
    and ( o.market_name = 'Match Winner'
       or o.market_name = 'Both Teams Score'
       or o.market_name = 'Double Chance'
       or (o.market_name = 'Goals Over/Under' and o.outcome_label in ('Over 2.5', 'Under 2.5'))
       or (o.market_name = 'Asian Handicap' and o.line_value between -2.5 and 2.5) )
$$ language sql
"""


def _rpc(corpo, nome="public.f(bigint)"):
    return (nome, corpo)


def _viola(corpo):
    return contrato_serving.mercados_fora_da_lista([_rpc(corpo)], SCHEMA, TABELA)


def test_a_rpc_de_cotacoes_de_hoje_so_cita_mercados_servidos():
    assert _viola(RPC_QUOTES) == {}


def test_citar_um_mercado_fora_da_lista_pelo_nome_e_apontado():
    corpo = RPC_QUOTES.replace("'Double Chance'", "'Corners Over Under'")

    assert _viola(corpo) == {"public.f(bigint)": ["Corners Over Under (id 45)"]}


def test_citar_pelo_id_fora_da_lista_tambem_e_apontado():
    corpo = "select 1 from futebol.fact_odds_snapshot o where o.market_id in (1, 4, 45)"

    assert _viola(corpo) == {"public.f(bigint)": ["market_id 45"]}


def test_citar_pelo_id_servido_nao_e_violacao():
    corpo = "select 1 from futebol.fact_odds_snapshot o where o.market_id = 6 or o.market_id in (1, 12)"

    assert _viola(corpo) == {}


def test_o_mercado_6_foi_mantido_pelo_victor_e_nao_e_violacao():
    corpo = "select 1 from futebol.fact_odds_snapshot where market_name = 'Goals Over/Under First Half'"

    assert _viola(corpo) == {}


def test_funcao_que_nao_le_a_tabela_de_odds_e_ignorada_mesmo_citando_o_nome():
    corpo = "select 'Exact Score' as rotulo from futebol.fact_fixtures"

    assert _viola(corpo) == {}


def test_o_mercado_nao_servido_e_conhecido_por_nome_e_id():
    """Os nomes dos mercados que a lista NÃO serve vêm do `WHERE market_id IN (...)` do dbt: o
    catálogo tem de ser disjunto da lista servida (um mercado é servido ou não)."""
    assert set(contrato_serving.MERCADOS_CONHECIDOS_NAO_SERVIDOS).isdisjoint(alvo.MERCADOS_SERVIDOS)
    assert contrato_serving.MERCADOS_CONHECIDOS_NAO_SERVIDOS[10] == "Exact Score"
    assert contrato_serving.MERCADOS_CONHECIDOS_NAO_SERVIDOS[45] == "Corners Over Under"


def test_mudar_a_lista_muda_o_veredito(monkeypatch):
    """A checagem lê a MESMA constante do sync: mover o 45 para servido o torna legítimo."""
    corpo = "select 1 from futebol.fact_odds_snapshot where market_name = 'Corners Over Under'"
    assert _viola(corpo)

    monkeypatch.setattr(alvo, "MERCADOS_SERVIDOS_NOMES", {**alvo.MERCADOS_SERVIDOS_NOMES, 45: "Corners Over Under"})
    monkeypatch.setattr(alvo, "MERCADOS_SERVIDOS", tuple(sorted(alvo.MERCADOS_SERVIDOS_NOMES)))

    assert _viola(corpo) == {}


class _Cursor:
    def __init__(self, funcoes):
        self._funcoes = funcoes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._funcoes


class _Conn:
    def __init__(self, funcoes):
        self._funcoes = funcoes

    def cursor(self):
        return _Cursor(self._funcoes)


def test_confere_le_as_funcoes_vivas_do_pg_proc_e_devolve_mensagens_legiveis():
    conn = _Conn([_rpc(RPC_QUOTES, "public.get_futebol_fixture_quotes(bigint)"),
                  _rpc("select 1 from futebol.fact_odds_snapshot where market_name = 'Exact Score'",
                       "public.get_futebol_exato(bigint)")])

    mensagens = contrato_serving.confere_mercados_servidos(conn)

    assert len(mensagens) == 1
    assert "get_futebol_exato(bigint)" in mensagens[0] and "Exact Score (id 10)" in mensagens[0]


@pytest.fixture
def script():
    return importlib.import_module("scripts.gera_contrato_serving")


def test_o_check_do_workflow_falha_com_1_se_uma_rpc_citar_mercado_fora_da_lista(script, monkeypatch, tmp_path):
    monkeypatch.setenv("CONTRATO_CHECK", "1")
    destino = tmp_path / "mapa.md"
    destino.write_text("igual", encoding="utf-8")
    monkeypatch.setattr(script, "DESTINO", destino)
    monkeypatch.setattr(script, "gera", lambda: "igual")
    monkeypatch.setattr(script, "confere_mercados_servidos", lambda: ["public.f(bigint): market_id 45"])

    assert script.main() == 1


def test_o_check_passa_quando_o_mapa_bate_e_nenhuma_rpc_foge_da_lista(script, monkeypatch, tmp_path):
    monkeypatch.setenv("CONTRATO_CHECK", "1")
    destino = tmp_path / "mapa.md"
    destino.write_text("igual", encoding="utf-8")
    monkeypatch.setattr(script, "DESTINO", destino)
    monkeypatch.setattr(script, "gera", lambda: "igual")
    monkeypatch.setattr(script, "confere_mercados_servidos", lambda: [])

    assert script.main() == 0


def test_fora_do_modo_check_o_script_so_regenera_o_mapa(script, monkeypatch, tmp_path):
    monkeypatch.delenv("CONTRATO_CHECK", raising=False)
    monkeypatch.setattr(script, "DESTINO", tmp_path / "mapa.md")
    monkeypatch.setattr(script, "gera", lambda: "novo")
    monkeypatch.setattr(script, "confere_mercados_servidos", lambda: pytest.fail("não deve conferir"))

    assert script.main() == 0
    assert (tmp_path / "mapa.md").read_text(encoding="utf-8") == "novo"
