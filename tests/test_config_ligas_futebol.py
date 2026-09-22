"""Invariante de registro de ligas do futebol (guard de expansão de campeonato).

A unidade de parametrização do futebol é a tupla `(league_id, season)`. Registrar uma liga
nova significa acrescentá-la a QUINZE listas de `src/config.py`, e esquecer uma delas hoje
não falha em lugar nenhum: o sintoma aparece dias depois como tabela vazia, premissa que
nunca dispara ou endpoint que ninguém coleta.

Os grupos têm semânticas DIFERENTES — e a diferença é o que estes testes fixam:

- ESPINHA ESTRITA (`FIXTURES`): obrigatória, sem exceção. Toda liga coletada precisa de
  jogo; as tuplas têm que ser IDÊNTICAS às de `LEAGUES_*` no mesmo modo.
- ESPINHA DE CATÁLOGO (`TEAMS`/`PLAYERS`): igual à espinha estrita, MENOS as ligas que
  declaram dispensar catálogo em `config.LEAGUES_SEM_CATALOGO_IDS` (ADR 0004, DE#92). A
  declaração é OBRIGATÓRIA nos dois sentidos — a liga declarada não pode faltar (permitido)
  nem pode aparecer (proibido; senão a declaração vira sugestão, não fato) — e a exceção é
  nominal, nunca geral: `test_excecao_de_catalogo_e_nominal_nao_geral` injeta um
  exception-set não vazio (o real está vazio até uma liga precisar) e prova as duas
  direções, incluindo que uma liga NÃO declarada continua acusando falta.
- OPT-IN por coverage (`STANDINGS`/`INJURIES`): SUBCONJUNTO, nunca igualdade. A exclusão é
  deliberada — a API não fornece o endpoint p/ aquela liga-temporada (ex.: mata-mata não tem
  classificação; a Copa do Mundo não tem desfalques) e incluir gastaria quota p/ voltar vazio.
- POLL pré-jogo (`FUTEBOL_*_LEAGUE_IDS`): só ids, e todo id tem que existir em
  `LEAGUES_CURRENT` — poll de liga que não é coletada é chamada garantidamente perdida.
"""
import pytest

from src import config


ESPINHA_ESTRITA = ["FIXTURES"]
ESPINHA_CATALOGO = ["TEAMS", "PLAYERS"]
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


def _faltando_de_catalogo(alvo_tuplas, referencia_tuplas, excecao_ids):
    """Tuplas de `referencia_tuplas` ausentes de `alvo_tuplas`, perdoando só as ligas
    nominalmente declaradas em `excecao_ids`. Extraída da asserção para que o teste de
    prova (`test_excecao_de_catalogo_e_nominal_nao_geral`) exercite a MESMA lógica usada
    pelo invariante, em vez de reimplementá-la.
    """
    exigida = {(league_id, season) for league_id, season in referencia_tuplas if league_id not in excecao_ids}
    return exigida - alvo_tuplas


def _presente_apesar_da_excecao(alvo_tuplas, excecao_ids):
    """Tuplas de `alvo_tuplas` cujo league_id está declarado em `excecao_ids` — presença
    que contradiz a própria declaração de dispensa. Mesma extração da função acima, e pelo
    mesmo motivo.
    """
    return {(league_id, season) for league_id, season in alvo_tuplas if league_id in excecao_ids}


# --------------------------------------------------------------------------- #
# Espinha estrita — igualdade, sem exceção
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("grupo", ESPINHA_ESTRITA)
@pytest.mark.parametrize("modo", MODOS)
def test_espinha_estrita_tem_as_mesmas_tuplas_de_leagues(grupo, modo):
    alvo, referencia = f"{grupo}_{modo}", f"LEAGUES_{modo}"
    faltando = _tuplas(referencia) - _tuplas(alvo)
    sobrando = _tuplas(alvo) - _tuplas(referencia)

    assert not faltando, (
        f"{alvo} não cobre {referencia}: faltam {sorted(faltando)}. "
        f"{grupo} é ESPINHA ESTRITA — toda liga coletada precisa dessa extração, sem exceção. "
        f"Acrescente a(s) tupla(s) em {alvo}."
    )
    assert not sobrando, (
        f"{alvo} tem tuplas que não existem em {referencia}: {sorted(sobrando)}. "
        f"Extrair {grupo.lower()} de liga-temporada que não é coletada é chamada perdida — "
        f"registre a liga em {referencia} ou remova de {alvo}."
    )


# --------------------------------------------------------------------------- #
# Espinha de catálogo — igualdade, exceto exceção NOMINAL declarada
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("grupo", ESPINHA_CATALOGO)
@pytest.mark.parametrize("modo", MODOS)
def test_catalogo_tem_as_mesmas_tuplas_de_leagues_exceto_declaradas(grupo, modo):
    alvo, referencia = f"{grupo}_{modo}", f"LEAGUES_{modo}"
    excecao_ids = set(config.LEAGUES_SEM_CATALOGO_IDS)
    faltando = _faltando_de_catalogo(_tuplas(alvo), _tuplas(referencia), excecao_ids)
    sobrando = _tuplas(alvo) - _tuplas(referencia)
    presente_apesar_da_excecao = _presente_apesar_da_excecao(_tuplas(alvo), excecao_ids)

    assert not faltando, (
        f"{alvo} não cobre {referencia}: faltam {sorted(faltando)}. "
        f"{grupo} é catálogo (espinha) — toda liga coletada precisa, a menos que declare "
        f"dispensa em config.LEAGUES_SEM_CATALOGO_IDS. Acrescente a(s) tupla(s) em {alvo} ou "
        f"declare a liga na exceção."
    )
    assert not sobrando, (
        f"{alvo} tem tuplas que não existem em {referencia}: {sorted(sobrando)}. "
        f"Extrair {grupo.lower()} de liga-temporada que não é coletada é chamada perdida — "
        f"registre a liga em {referencia} ou remova de {alvo}."
    )
    assert not presente_apesar_da_excecao, (
        f"{alvo} tem tuplas de liga(s) declarada(s) em LEAGUES_SEM_CATALOGO_IDS: "
        f"{sorted(presente_apesar_da_excecao)}. A declaração diz que a liga dispensa "
        f"catálogo — presença contradiz a própria declaração. Remova a(s) tupla(s) de {alvo} "
        f"ou tire a liga de LEAGUES_SEM_CATALOGO_IDS."
    )


def test_excecao_de_catalogo_e_nominal_nao_geral():
    """Prova as duas obrigações da exceção nominal (ADR 0004, DE#92) com um exception-set
    NÃO vazio injetado — `LEAGUES_SEM_CATALOGO_IDS` está vazio até uma liga precisar, então
    rodar contra o config real não exercitaria o mecanismo:

    1. A liga declarada some de `faltando` mesmo ausente do catálogo (a exceção libera).
    2. A liga declarada aparece em `presente_apesar_da_excecao` se estiver no catálogo
       mesmo assim (a exceção também PROÍBE presença — não é permissão frouxa).
    3. Uma liga NÃO declarada continua acusando falta ao ser removida — a exceção não vaza
       para quem não a pediu.
    """
    referencia = _tuplas("LEAGUES_CURRENT")
    declarada, nao_declarada = list(referencia)[:2]
    excecao_ids = {declarada[0]}

    alvo_sem_as_duas = referencia - {declarada, nao_declarada}

    faltando = _faltando_de_catalogo(alvo_sem_as_duas, referencia, excecao_ids)
    assert declarada not in faltando, (
        f"Liga declarada em excecao_ids ({declarada}) não deveria acusar falta mesmo "
        f"ausente do catálogo — é exatamente isso que a declaração permite."
    )
    assert nao_declarada in faltando, (
        f"Liga NÃO declarada ({nao_declarada}) removida do catálogo deveria continuar "
        f"acusando falta — a exceção não pode vazar para ligas que não a pediram."
    )

    presente_apesar_da_excecao = _presente_apesar_da_excecao(referencia, excecao_ids)
    assert declarada in presente_apesar_da_excecao, (
        f"Liga declarada em excecao_ids ({declarada}) presente no catálogo deveria ser "
        f"sinalizada — a declaração também PROÍBE presença, não é só permissão de ausência."
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
