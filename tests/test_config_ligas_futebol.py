"""Invariante de registro de ligas do futebol (guard de expansão de campeonato).

A unidade de parametrização do futebol é a tupla `(league_id, season)`. Registrar uma liga
nova significa acrescentá-la a QUINZE listas de `src/config.py`, e esquecer uma delas hoje
não falha em lugar nenhum: o sintoma aparece dias depois como tabela vazia, premissa que
nunca dispara ou endpoint que ninguém coleta.

Os três grupos têm semânticas DIFERENTES — e a diferença é o que estes testes fixam:

- ESPINHA (`TEAMS`/`PLAYERS`/`FIXTURES`): obrigatória. Toda liga coletada precisa de time,
  jogador e jogo; as tuplas têm que ser IDÊNTICAS às de `LEAGUES_*` no mesmo modo.
- OPT-IN por coverage (`STANDINGS`/`INJURIES`): SUBCONJUNTO, nunca igualdade. A exclusão é
  deliberada — a API não fornece o endpoint p/ aquela liga-temporada (ex.: mata-mata não tem
  classificação; a Copa do Mundo não tem desfalques) e incluir gastaria quota p/ voltar vazio.
- POLL pré-jogo (`FUTEBOL_*_LEAGUE_IDS`): só ids, e todo id tem que existir em
  `LEAGUES_CURRENT` — poll de liga que não é coletada é chamada garantidamente perdida.
"""
import pytest

from src import config


ESPINHA = ["TEAMS", "PLAYERS", "FIXTURES"]
OPT_IN = ["STANDINGS", "INJURIES"]
MODOS = ["BACKFILL", "CURRENT"]

POLLS = [
    "FUTEBOL_ODDS_LEAGUE_IDS",
    "FUTEBOL_PREDICTIONS_LEAGUE_IDS",
    "FUTEBOL_INJURIES_LEAGUE_IDS",
]


def _tuplas(nome):
    return set(getattr(config, nome))


def _ids(tuplas):
    return {league_id for league_id, _ in tuplas}


# --------------------------------------------------------------------------- #
# Espinha — igualdade
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("grupo", ESPINHA)
@pytest.mark.parametrize("modo", MODOS)
def test_espinha_tem_as_mesmas_tuplas_de_leagues(grupo, modo):
    alvo, referencia = f"{grupo}_{modo}", f"LEAGUES_{modo}"
    faltando = _tuplas(referencia) - _tuplas(alvo)
    sobrando = _tuplas(alvo) - _tuplas(referencia)

    assert not faltando, (
        f"{alvo} não cobre {referencia}: faltam {sorted(faltando)}. "
        f"{grupo} é ESPINHA — toda liga coletada precisa dessa extração. "
        f"Acrescente a(s) tupla(s) em {alvo}."
    )
    assert not sobrando, (
        f"{alvo} tem tuplas que não existem em {referencia}: {sorted(sobrando)}. "
        f"Extrair {grupo.lower()} de liga-temporada que não é coletada é chamada perdida — "
        f"registre a liga em {referencia} ou remova de {alvo}."
    )


# --------------------------------------------------------------------------- #
# Opt-in por coverage — subconjunto (a exclusão é deliberada, não pode ser forçada)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("grupo", OPT_IN)
@pytest.mark.parametrize("modo", MODOS)
def test_optin_e_subconjunto_de_leagues(grupo, modo):
    alvo, referencia = f"{grupo}_{modo}", f"LEAGUES_{modo}"
    sobrando = _tuplas(alvo) - _tuplas(referencia)

    assert not sobrando, (
        f"{alvo} tem tuplas ausentes de {referencia}: {sorted(sobrando)}. "
        f"{grupo} é OPT-IN por coverage, mas só entre as ligas efetivamente coletadas — "
        f"registre a liga em {referencia} ou remova de {alvo}."
    )


# --------------------------------------------------------------------------- #
# Poll pré-jogo — só ids, e todos coletados na temporada corrente
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lista", POLLS)
def test_poll_so_aponta_p_liga_coletada_na_corrente(lista):
    orfaos = set(getattr(config, lista)) - _ids(_tuplas("LEAGUES_CURRENT"))

    assert not orfaos, (
        f"{lista} aponta p/ liga(s) fora de LEAGUES_CURRENT: {sorted(orfaos)}. "
        f"O poll pré-jogo filtra os jogos NS por esses ids — liga não coletada nunca terá "
        f"jogo NS na base, então a entrada é morta. Registre em LEAGUES_CURRENT ou remova."
    )
