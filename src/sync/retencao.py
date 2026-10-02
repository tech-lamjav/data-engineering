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

Em DEV a regra vale sempre. PRD só recebe regra nas odds, e só quando o workflow liga a tabela
(`cache_serving`, lançamento escuro da DE#109, ADR 0006): o Postgres de PRD vira cache de
serving. Mercados servidos (`alvo.MERCADOS_SERVIDOS`) valem nos dois ambientes; a retenção de
produto de 30 dias, com o fechamento (T-15m) das fixtures mais antigas, só em PRD.

As constantes leem env var com default; o serviço de sync não as define no deploy, então valem
os defaults. São lidas na CHAMADA do resolvedor (não copiadas para as regras na importação),
para que uma constante mude sem a outra e o teste possa trocar o valor.

Sem dependência de banco nem de nuvem (só `src.config` e `src.sync.alvo`).
"""
import os

from src.config import get_dev_retention_rule
from src.sync.alvo import MERCADOS_SERVIDOS, SYNC_EXCLUSOES  # noqa: F401 (reexporta: uma constante só)

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

# Cache de serving das odds em PRD (DE#109). A janela de FECHAMENTO é a banda t15m (0-15 min antes
# do apito, a linha de CLV; `src.config.FUTEBOL_ODDS_WINDOWS`): fixture mais antiga que a
# retenção de produto fica só com ela. O corte usa o kickoff de `fact_fixtures` (não o das odds:
# medido, 8 fixtures divergem).
JANELA_FECHAMENTO = "t15m"

# Tabelas de coleta cujo número de dias vem da retenção de coleta (a coluna do corte continua
# a do `config.py`), e a coluna de partição que o filtro no BigQuery usa para não ler dias
# inteiros que o corte já descarta (`fact_odds_snapshot` é particionada por `collection_date`).
TABELA_ODDS = "fact_odds_snapshot"
TABELAS_RETENCAO_COLETA_FUTEBOL = {
    "fact_odds_snapshot": {"partition_column": "collection_date"},
    "fact_injuries_snapshot": {},
}


def resolve_regra_retencao(
    sport: str, env: str, table_name: str, cache_serving: frozenset = frozenset()
) -> dict | None:
    """Regra de retenção para (esporte, ambiente, tabela), ou None.

    Mesmo contrato de `config.get_dev_retention_rule` (que ela substitui no sync) mais o cache de
    serving de PRD (DE#109):
    - PRD: None, EXCETO `fact_odds_snapshot` do futebol quando está em `cache_serving` (a lista
      que o workflow liga): regra `cache_serving`, com mercados servidos, as fixtures dos
      últimos `RETENCAO_PRODUTO_DIAS_ATRAS` dias e futuras com todas as janelas, e as mais
      antigas só com a janela de fechamento. Qualquer outro ambiente desconhecido: None;
    - tabela que o sync não copia mais (exclusões de `alvo.py`): None;
    - tabela de coleta: a regra do config com `days` = retenção de coleta;
    - tabela de produto do futebol: corte por fixture, `days` para trás e `days_ahead` à frente;
    - o resto: o que o `config.py` já tem (as regras por temporada) ou None.
    """
    env = (env or "").lower()
    sport = (sport or "nba").lower()
    if table_name in SYNC_EXCLUSOES.get(sport, frozenset()):
        return None
    if env == "prd":
        if sport == "futebol" and table_name == TABELA_ODDS and table_name in cache_serving:
            return {
                "kind": "cache_serving",
                "column": "fixture_id",
                "days": RETENCAO_PRODUTO_DIAS_ATRAS,
                "market_column": "market_id",
                "market_ids": MERCADOS_SERVIDOS,
                "closing_column": "collection_window",
                "closing_window": JANELA_FECHAMENTO,
                "fixtures_table": "fact_fixtures",
                "kickoff_column": "kickoff_utc",
            }
        return None
    if env != "dev":
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
            regra = {
                **base,
                "days": RETENCAO_COLETA_DIAS,
                **TABELAS_RETENCAO_COLETA_FUTEBOL[table_name],
            }
            if table_name == TABELA_ODDS:
                # Mercados servidos também em DEV (DE#109, história 37): lê o que o app lê.
                regra["market_column"] = "market_id"
                regra["market_ids"] = MERCADOS_SERVIDOS
            return regra

    return get_dev_retention_rule(sport, env, table_name)
