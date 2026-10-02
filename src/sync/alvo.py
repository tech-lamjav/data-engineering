"""Resolvedor único do alvo do sync: a allowlist do esporte MENOS as exclusões (DE#112).

Três consumidores enumeram "o que o sync copia" e precisam enxergar o MESMO conjunto:
o próprio sync (`bq_to_postgres.run_sync`), o detector de atraso (`monitoring/atraso_sync`)
e o gerador do contrato de serving (`monitoring/contrato_serving`). Se só o sync passasse a
pular uma tabela, o detector continuaria medindo o atraso dela, e o atraso só cresce: ele
acenderia vermelho para sempre por uma tabela que ninguém mais copia. Por isso a exclusão
mora aqui, e os três chamam `resolve_alvo_sync` em vez de `config.get_sync_target`
(`tests/test_sync_alvo.py` falha se algum voltar a ler a allowlist crua).

POR QUE AS EXCLUSÕES NÃO MORAM EM `src/config.py`: o módulo de configuração entra no
carimbo de procedência dos 29 serviços Cloud Run (ADR 0001). Editá-lo para tirar uma tabela
do sync deixaria a frota inteira em deriva até um redeploy completo. Este módulo mora em
`src/sync/`, que só o serviço `sync-bq-to-postgres` declara no manifesto. A allowlist e as
constantes de retenção seguem em `config.py`, intocadas.

Sem dependência de banco nem de nuvem (só `src.config`): o detector e o gerador rodam no
GitHub Actions e os testes das funções puras não podem exigir psycopg nem o SDK do Google.
"""
from src.config import get_sync_target

# Exclusões por esporte: tabelas que continuam na allowlist de `config.py` mas que o sync
# não copia mais. A razão de cada uma fica no comentário ao lado (sem ADR: reversível).
SYNC_EXCLUSOES: dict[str, frozenset[str]] = {
    "nba": frozenset(),
    "futebol": frozenset(
        {
            # `int_futebol_odds_devig` (~811 mil linhas, ~181 MB no PRD) era copiada a cada
            # sync e travava (TRUNCATE + COPY) sem leitor conhecido: nenhuma RPC a lê desde
            # a migration 105 do app, e o Victor conferiu app, edge functions e scripts
            # (prop-play-predictor#542, resposta 5, 30/09/2026). Continua existindo no
            # BigQuery, onde o dbt a constrói. As cópias no Postgres (PRD e DEV) ficam
            # congeladas por ~7 dias; o `DROP` é DDL do app, em ticket separado, e só
            # acontece se `seq_scan` estiver estável em duas leituras
            # (scripts/leituras_copia_devig.py). Reverter = remover esta linha.
            "int_futebol_odds_devig",
        }
    ),
}


def resolve_alvo_sync(sport: str = "nba") -> tuple:
    """(dataset BQ, schema Postgres, tabelas ordenadas) do que o sync copia de fato.

    Mesmo contrato de `config.get_sync_target`, menos as tabelas de `SYNC_EXCLUSOES`.
    A ordem canônica (dim -> fact -> mart) é preservada. Exclusão que não existe na
    allowlist do esporte levanta ValueError: seria typo, e um resolvedor inofensivo faria
    a tabela continuar sendo copiada sem ninguém perceber.
    """
    dataset, schema, tabelas = get_sync_target(sport)
    excluidas = SYNC_EXCLUSOES.get((sport or "nba").lower(), frozenset())
    inexistentes = sorted(excluidas - set(tabelas))
    if inexistentes:
        raise ValueError(
            f"Exclusões fora da allowlist do sync de {sport!r}: {inexistentes}"
        )
    return dataset, schema, [t for t in tabelas if t not in excluidas]
