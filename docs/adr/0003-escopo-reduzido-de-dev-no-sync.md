# DEV deixa de ser espelho de escopo completo em 5 tabelas de alto crescimento

**Status:** accepted (2026-09-09)
**Issue:** [DE #75](https://github.com/tech-lamjav/data-engineering/issues/75) (fatias [DE #76](https://github.com/tech-lamjav/data-engineering/issues/76), [DE #77](https://github.com/tech-lamjav/data-engineering/issues/77))

## Contexto

O sync BigQuery→Postgres (`src/sync/bq_to_postgres.py`) trazia o mesmo escopo completo — todas
as ligas, histórico integral, full-refresh — tanto para PRD quanto para DEV; o parâmetro `env`
só trocava a connection string. Em 08/09/2026 isso estourou o teto de 500 MB do free tier do
projeto DEV/staging (1214 MB), corrigido na hora com um purge manual + o cron `purge-old-snapshots`
(job 12, diário às 04:00 UTC) — ver memory `project_supabase_dev_free_overage_202609`. O purge
funciona como rede de segurança, mas é remédio a jusante: o sync continuava trazendo do BigQuery e
escrevendo no Postgres, a cada execução, linhas que o cron apagaria poucas horas depois.

## Decisão

O motor de sync ganhou um filtro de linha opcional, resolvido por (esporte, ambiente, tabela),
aplicado durante a cópia — antes do `write_row`, depois do skip-if-unchanged e do parity check.
Ativo **só** em DEV, nas mesmas 5 tabelas que o purge de 08/09 já cobria:

| tabela | forma da regra | corte |
|---|---|---|
| `fact_odds_snapshot` | coluna própria | `collection_timestamp` ≥ hoje − 14 dias |
| `fact_injuries_snapshot` | coluna própria | `snapshot_date` ≥ hoje − 14 dias |
| `fact_fixture_player_stats` | coluna própria | `season` = temporada corrente |
| `fact_fixture_lineups_players` | coluna própria | `season` = temporada corrente |
| `int_futebol_odds_devig` | lookup cross-table | `fixture_id` ∈ fixtures com `kickoff_utc` ≥ hoje − 14 dias (via `fact_fixtures`, já sincronizada na mesma execução) |

**Os números (14 dias; temporada corrente) são reusados do purge de 08/09/2026, não
re-derivados.** Já estavam validados em produção; inventar um número novo e não testado não
teria benefício e adicionaria risco.

PRD nunca usa este filtro — nenhuma tabela, nenhum ambiente fora de DEV. Qualquer tabela sem
regra configurada (as outras 16 do futebol) continua recebendo 100% das linhas, em qualquer
ambiente. A estrutura de configuração existe para o NBA (mecanismo é sport-agnostic) mas fica
vazia — sem vertical NBA ativa, não há número real a validar.

O cron `purge-old-snapshots` **continua rodando sem alteração**, como rede de segurança caso o
filtro do sync seja desabilitado, mal configurado, ou surja um padrão de crescimento fora dele.

## Consequências

- `CONTEXT.md` (verbete **Sync**) deixa de presumir que PRD e DEV recebem sempre o mesmo escopo.
- `int_futebol_odds_devig` depende de `fact_fixtures` estar na mesma execução do sync (ordem já
  garantida por `FUTEBOL_SYNC_TABLES_ORDERED`); sincronizá-la sozinha em DEV sem `fact_fixtures`
  falha explicitamente (`RuntimeError`) em vez de produzir um filtro silenciosamente vazio.
- Fora de escopo, por decisão explícita: mexer no escopo de PRD (mesma curva de crescimento sem
  teto, decisão de 08/09/2026 de não tocar produção), revisar os números de retenção em si, e
  estender o mecanismo com números reais de NBA.
