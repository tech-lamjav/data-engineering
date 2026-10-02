"""Linha de base de leituras da cópia congelada do devig (DE#112, história 43).

O `DROP` da cópia só acontece se ninguém a estiver lendo. A evidência são duas leituras de
`pg_stat_user_tables`, no dia do congelamento e 7 dias depois; estas funções decidem o que
duas leituras dizem. Nada aqui toca banco de produção: o que toca é o script, e ele só roda
por mão humana.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.monitoring.leituras_copia import (
    Leitura,
    compara,
    de_json,
    indexa_por_env,
    le_leitura,
    para_json,
)

T0 = datetime(2026, 10, 2, 13, 0, tzinfo=timezone.utc)


def _leitura(seq=14500, idx=3, linhas=811_000, quando=T0, env="prd"):
    return Leitura(
        env=env, medido_em=quando, seq_scan=seq, idx_scan=idx, n_live_tup=linhas
    )


# ------------------------------------------------------------------
# compara: o que duas leituras dizem sobre o DROP
# ------------------------------------------------------------------
def test_contadores_parados_depois_de_7_dias_liberam_o_drop():
    base = _leitura()
    depois = _leitura(quando=T0 + timedelta(days=7))

    r = compara(base, depois)

    assert r.veredito == "estavel"
    assert (r.delta_seq_scan, r.delta_idx_scan) == (0, 0)


def test_seq_scan_subiu_alguem_le_a_copia():
    r = compara(_leitura(), _leitura(seq=14530, quando=T0 + timedelta(days=7)))

    assert r.veredito == "lida"
    assert r.delta_seq_scan == 30


def test_idx_scan_subiu_tambem_conta_como_leitura():
    r = compara(_leitura(), _leitura(idx=4, quando=T0 + timedelta(days=8)))

    assert r.veredito == "lida"
    assert r.delta_idx_scan == 1


def test_menos_de_7_dias_nao_conclui_mesmo_com_contador_parado():
    r = compara(_leitura(), _leitura(quando=T0 + timedelta(days=6, hours=23)))

    assert r.veredito == "cedo"


def test_contador_que_diminuiu_e_estatistica_zerada_e_nao_estabilidade():
    # pg_stat_reset ou crash do servidor zeram os contadores: "0 de diferença" viria de
    # contadores recomeçando, não de ninguém ter lido. Medição inválida, repetir a base.
    r = compara(_leitura(seq=14500), _leitura(seq=12, quando=T0 + timedelta(days=7)))

    assert r.veredito == "invalida"


def test_leituras_de_ambientes_diferentes_nao_se_comparam():
    r = compara(_leitura(env="prd"), _leitura(env="dev", quando=T0 + timedelta(days=7)))

    assert r.veredito == "invalida"


def test_repeticao_anterior_a_base_e_invalida():
    r = compara(_leitura(), _leitura(quando=T0 - timedelta(days=1)))

    assert r.veredito == "invalida"


def test_linhas_que_mudaram_aparecem_no_resultado_sem_mudar_o_veredito():
    # n_live_tup é estimativa do autovacuum; serve de sinal de que o sync ainda escreve na
    # cópia (imagem velha), mas não decide o DROP sozinho.
    r = compara(_leitura(), _leitura(linhas=0, quando=T0 + timedelta(days=7)))

    assert r.veredito == "estavel"
    assert r.delta_n_live_tup == -811_000


# ------------------------------------------------------------------
# Registro: ida e volta em JSON (é o que o humano cola no ticket e relê 7 dias depois)
# ------------------------------------------------------------------
def test_json_ida_e_volta_preserva_a_leitura():
    base = _leitura()

    assert de_json(para_json(base)) == base


def test_json_traz_o_momento_em_utc_iso():
    assert '"medido_em": "2026-10-02T13:00:00+00:00"' in para_json(_leitura())


def test_de_json_rejeita_registro_sem_campo():
    with pytest.raises(ValueError):
        de_json('{"env": "prd", "seq_scan": 1}')


# ------------------------------------------------------------------
# le_leitura: a consulta é só SELECT na pg_stat_user_tables
# ------------------------------------------------------------------
class _Cur:
    def __init__(self, linhas):
        self._linhas = linhas
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class _Conn:
    def __init__(self, linhas):
        self.cur = _Cur(linhas)

    def cursor(self):
        return self.cur


def test_le_leitura_devolve_os_tres_contadores_e_o_momento():
    conn = _Conn([(T0, 14500, 3, 811_000)])

    leitura = le_leitura(conn, "prd", "futebol", "int_futebol_odds_devig")

    assert leitura == _leitura()
    assert conn.cur.params == ("futebol", "int_futebol_odds_devig")
    sql = conn.cur.sql.lower()
    assert sql.lstrip().startswith("select") and "pg_stat_user_tables" in sql


def test_tabela_que_nao_aparece_nas_estatisticas_e_erro_nao_zero():
    # Sem linha, imprimir zeros pareceria "ninguém lê": a tabela pode ter sido
    # dropada, renomeada ou estar em outro schema.
    with pytest.raises(LookupError, match="int_futebol_odds_devig"):
        le_leitura(_Conn([]), "prd", "futebol", "int_futebol_odds_devig")


def test_contador_nulo_e_erro():
    # Papel sem visibilidade das estatísticas devolve NULL; tratar como 0 daria um
    # "estável" falso.
    with pytest.raises(LookupError):
        le_leitura(_Conn([(T0, None, None, None)]), "prd", "futebol", "int_futebol_odds_devig")


# ------------------------------------------------------------------
# Arquivo de registros: uma linha por ambiente
# ------------------------------------------------------------------
def test_registros_sao_indexados_por_ambiente_ignorando_linhas_em_branco():
    prd, dev = _leitura(env="prd"), _leitura(env="dev", seq=7)
    texto = para_json(prd) + "\n\n" + para_json(dev) + "\n"

    assert indexa_por_env(texto) == {"prd": prd, "dev": dev}


def test_dois_registros_do_mesmo_ambiente_sao_ambiguos():
    texto = para_json(_leitura()) + "\n" + para_json(_leitura(seq=1))

    with pytest.raises(ValueError, match="prd"):
        indexa_por_env(texto)


# ------------------------------------------------------------------
# Script: o stdout é só o arquivo de registros (o runbook redireciona `> dia0.jsonl`)
# ------------------------------------------------------------------
def test_script_com_falha_nao_escreve_log_nem_traceback_no_stdout():
    """Falha de ambiente (URL ausente) vai para o stderr e sai com 2.

    O logger do repo escreve em stdout; sem desviar, o arquivo redirecionado ficaria com
    `ERROR - ...` e o traceback no meio do JSONL, e a repetição de 7 dias quebraria no parse.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    env = {k: v for k, v in os.environ.items() if not k.startswith("SUPABASE_PG_URL")}
    env["LEITURA_ENVS"] = "dev"
    env.pop("LEITURA_ANTERIOR", None)

    r = subprocess.run(
        [sys.executable, str(raiz / "scripts" / "leituras_copia_devig.py")],
        capture_output=True, text=True, cwd=raiz, env=env,
    )

    assert r.returncode == 2
    assert r.stdout == ""
    assert "SUPABASE_PG_URL_DEV" in r.stderr
