"""Retenção de DEV do sync: números, regras novas e o resolvedor por (esporte, ambiente, tabela).

POR QUE ISTO MORA AQUI E NÃO EM `src/config.py` (DE#106, DE#112): o `config.py` entra no
carimbo de procedência dos 29 serviços Cloud Run (ADR 0001). Editá-lo, nem que seja uma
constante ou um comentário, deixa a frota inteira em deriva até um redeploy completo. Este
módulo mora em `src/sync/`, que só o serviço `sync-bq-to-postgres` declara no manifesto.

O que ficou em `config.py`, intocado: `SYNC_DEV_RETENTION_RULES` (as cinco regras de 09/09,
inclusive a do de-vig, hoje morta porque a tabela saiu do sync em `alvo.SYNC_EXCLUSOES`),
`SYNC_DEV_RETENTION_DAYS = 14` e `get_dev_retention_rule`. Aquele 14 NÃO vale mais para odds
e desfalques: o resolvedor daqui sobrepõe pelo número da retenção de coleta. Limpar o
`config.py` (apagar a regra do de-vig e o 14) fica para uma mudança que já exija o redeploy
da frota inteira.

Duas famílias de retenção (verbete **Retenção** do CONTEXT.md), cada uma com a sua constante:

- **Retenção de coleta**: contada pelo momento da captura (snapshots de odds e de desfalques).
  14 dias -> 7: a maior tabela do DEV é `fact_odds_snapshot` (159 MB) e ela dita o pico do
  sync (DE#106, história 6).
- **Retenção de produto**: contada pelo kickoff da fixture, de 30 dias atrás até 14 dias à
  frente, nas tabelas que o app lê por fixture (`fact_insumos_medidos`, as cinco
  `int_futebol_premissas_*` e `fact_value_opportunities_hist`). O corte para frente existe
  porque, medido em 28/09, 75% do que sobraria de valor medido com corte só para trás eram
  fixtures a mais de 14 dias.

Só vale em DEV. PRD nunca recebe regra daqui: o que muda PRD nas odds é a #109 (ADR 0006).

As constantes leem env var com default; o serviço de sync não as define no deploy, então valem
os defaults. São lidas na CHAMADA do resolvedor (não copiadas para as regras na importação),
para que uma constante mude sem a outra e o teste possa trocar o valor.

Sem dependência de banco nem de nuvem (só `src.config` e `src.sync.alvo`).
"""
import os

from src.config import get_dev_retention_rule
from src.sync.alvo import SYNC_EXCLUSOES

# Retenção de coleta (dias contados a partir do instante da captura).
RETENCAO_COLETA_DIAS = int(os.getenv("SYNC_RETENCAO_COLETA_DIAS", "7"))

# Retenção de produto (dias em torno do kickoff da fixture).
RETENCAO_PRODUTO_DIAS_ATRAS = int(os.getenv("SYNC_RETENCAO_PRODUTO_DIAS_ATRAS", "30"))
RETENCAO_PRODUTO_DIAS_A_FRENTE = int(os.getenv("SYNC_RETENCAO_PRODUTO_DIAS_A_FRENTE", "14"))

# Tabelas de produto do futebol com retenção por fixture. `fact_value_opportunities` (o
# board) NÃO entra: é pequena e não acumula histórico. Todas dependem de `fact_fixtures`
# sincronizada na mesma execução (de lá saem os fixture_id elegíveis).
TABELAS_RETENCAO_PRODUTO_FUTEBOL = (
    "fact_insumos_medidos",
    "int_futebol_premissas_1x2",
    "int_futebol_premissas_ou",
    "int_futebol_premissas_ah",
    "int_futebol_premissas_btts",
    "int_futebol_premissas_dc",
    "fact_value_opportunities_hist",
)

# Tabelas de coleta cujo número de dias vem da retenção de coleta (a coluna do corte continua
# a do `config.py`), e a coluna de partição que o filtro no BigQuery usa para não ler dias
# inteiros que o corte já descarta (`fact_odds_snapshot` é particionada por `collection_date`).
TABELAS_RETENCAO_COLETA_FUTEBOL = {
    "fact_odds_snapshot": {"partition_column": "collection_date"},
    "fact_injuries_snapshot": {},
}


def resolve_regra_retencao(sport: str, env: str, table_name: str) -> dict | None:
    """Regra de retenção de DEV para (esporte, ambiente, tabela), ou None.

    Mesmo contrato de `config.get_dev_retention_rule` e o substitui no sync:
    - fora de DEV (PRD e qualquer ambiente desconhecido): sempre None;
    - tabela que o sync não copia mais (exclusões de `alvo.py`): None;
    - tabela de coleta: a regra do config com `days` = retenção de coleta;
    - tabela de produto do futebol: corte por fixture, `days` para trás e `days_ahead` à frente;
    - o resto: o que o `config.py` já tem (as regras por temporada) ou None.
    """
    if (env or "").lower() != "dev":
        return None
    sport = (sport or "nba").lower()
    if table_name in SYNC_EXCLUSOES.get(sport, frozenset()):
        return None

    if sport == "futebol":
        if table_name in TABELAS_RETENCAO_PRODUTO_FUTEBOL:
            return {
                "kind": "fixture_window",
                "column": "fixture_id",
                "days": RETENCAO_PRODUTO_DIAS_ATRAS,
                "days_ahead": RETENCAO_PRODUTO_DIAS_A_FRENTE,
                "requires": "fact_fixtures",
            }
        if table_name in TABELAS_RETENCAO_COLETA_FUTEBOL:
            base = get_dev_retention_rule(sport, env, table_name)
            return {
                **base,
                "days": RETENCAO_COLETA_DIAS,
                **TABELAS_RETENCAO_COLETA_FUTEBOL[table_name],
            }

    return get_dev_retention_rule(sport, env, table_name)


# Mercados servidos (DE#112/#109, ADR 0006): os `market_id` de `fact_odds_snapshot` que o app lê
# ou que o dono do app decidiu manter. UMA constante, fácil de mudar. A lista abaixo é a decisão
# do Victor de 30/09/2026 (prop-play-predictor#542): tirar 10, 7, 57, 58 e 77; escanteios 45 e 56
# saem do Postgres de PRD; MANTER o 6 (Gols mais/menos no 1º tempo), por plano e não por uso. Ele
# pede o volume do 6 e reconsidera se for caro: medido em 01/10, o mercado 6 tem 329.756 linhas
# (7,70% da tabela), +24% sobre os cinco mercados 1, 4, 5, 8 e 12.
#
# ESTA FATIA (DE#106) NÃO APLICA O FILTRO DE MERCADOS. Ela só define a constante, para a #109 (que
# muda PRD nas odds e leva o filtro de mercados também ao DEV) importá-la daqui em vez de
# redigitar a lista. O que a DE#106 faz com as odds é a retenção de coleta de 7 dias.
MERCADOS_SERVIDOS = (1, 4, 5, 6, 8, 12)
