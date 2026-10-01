# DEV deixa de ser espelho de escopo completo em 5 tabelas de alto crescimento

**Status:** accepted (2026-09-09) — números e papel da limpeza revistos em 2026-10-01, ver "Emenda (DE#106, 2026-10-01)"; a cláusula "PRD nunca usa este filtro" será revogada para `fact_odds_snapshot` pela [ADR 0006](0006-prd-cache-de-serving-para-odds.md) (2026-09-29); para DEV, segue valendo; a regra de `int_futebol_odds_devig` deixou de valer quando a tabela saiu do sync (DE#112, fatia 0)
**Issue:** [DE #75](https://github.com/tech-lamjav/data-engineering/issues/75) (fatias [DE #76](https://github.com/tech-lamjav/data-engineering/issues/76), [DE #77](https://github.com/tech-lamjav/data-engineering/issues/77)) · emenda: [DE #106](https://github.com/tech-lamjav/data-engineering/issues/106)

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

## Emenda (DE#106, 2026-10-01) — os números e a "rede de segurança" foram reabertos

Medido em 28/09/2026, o DEV voltou a 654–669 MB em repouso (picos de 810 MB), acima do teto de
500 MB do free tier. A decisão acima **continua valendo** (retenção só em DEV, aplicada na carga),
mas dois pontos dela não: os números e o papel da limpeza do cron. O corpo original acima não foi
reescrito; o que mudou está aqui.

**1. A limpeza do job 12 nunca foi rede de segurança.** O texto acima diz que o `purge-old-snapshots`
"continua rodando como rede de segurança caso o filtro do sync seja desabilitado". Não era: o sync faz
TRUNCATE + COPY, então o espaço volta a cada recarga e os `DELETE` de futebol do job repetiam cortes
que o sync já aplica (não liberavam nada). E, se a retenção do sync sumisse, o sync recopiaria tudo a
cada mudança no BigQuery e o job apagaria de novo, num ciclo. O job 12 mantém o número, o nome e o
horário (04:00 UTC) mas passa a limpar **só** o `cron.job_run_details` (SQL versionado em
`scripts/sql/job12_purge_so_job_run_details.sql`, aplicado à mão no DEV). A proteção real passa a
ser o **alerta de tamanho do resumo diário** (verbete **Teto do DEV**): o sync mede o DEV ao fim do passe DEV (soma de
`pg_database_size` de todos os bancos, a métrica do teto) e o resumo alerta acima de 450 MB, com `[DEV]`
no assunto. Dia sem leitura é seção degradada, nunca silêncio.

**2. Os números, e a entrada de sete tabelas de produto.** A causa do excesso eram tabelas de produto sem
regra nenhuma, que chegavam 100% ao DEV. Duas famílias de retenção (verbete **Retenção** do
`CONTEXT.md`), cada uma com a sua constante em `src/sync/retencao.py`:

| tabela | família | corte |
|---|---|---|
| `fact_odds_snapshot` | coleta | `collection_timestamp` ≥ agora − **7** dias (era 14) |
| `fact_injuries_snapshot` | coleta | `snapshot_date` ≥ hoje − **7** dias (era 14) |
| `fact_insumos_medidos` | produto | `fixture_id` ∈ fixtures com kickoff de **−30 a +14** dias |
| `int_futebol_premissas_{1x2,ou,ah,btts,dc}` | produto | idem |
| `fact_value_opportunities_hist` | produto | idem (todas as versões de uma fixture elegível ficam juntas) |
| `fact_fixture_player_stats`, `fact_fixture_lineups_players` | temporada | inalterado: temporada corrente |

O corte para frente existe de fato: medido em 28/09, 75% do que sobraria de valor medido com um corte
só para trás eram fixtures a mais de 14 dias. Os 14 dias de coleta caíram para 7 porque a maior tabela
do DEV, `fact_odds_snapshot` (159 MB), dita o pico do sync. As duas tabelas por temporada **não** mudaram
de regra: não são parte do problema.

**3. A regra do de-vig deixou de valer.** `int_futebol_odds_devig` saiu do sync nos dois ambientes
(DE#112, fatia 0): não entra mais na retenção, e a cópia que ficou no Postgres fica congelada até o
`DROP` (DDL do app, ticket à parte). A entrada dela em `SYNC_DEV_RETENTION_RULES` (`src/config.py`)
ficou **morta e intocada**; ver abaixo.

**4. O filtro roda no BigQuery.** Antes, a retenção filtrava em Python depois de `list_rows` ler a
tabela inteira (4,16 mi de linhas lidas para gravar 698 mil, em odds). Agora a tabela com regra, em
DEV, é lida por query job parametrizado (`src/sync/filtro_bq.py`, reutilizável: a #109 o compõe para
PRD). Isso custa bytes faturados (teto por job = 2× o tamanho lógico da tabela; `fact_odds_snapshot` é
particionada por `collection_date` e o corte de 7 dias poda partições: ~62 MB lidos de ~720 MB) e **exige
`bigquery.jobs.create` na conta de runtime do sync**, que não tem. Sem a permissão o passe DEV aborta com
403, num pré-voo (dry-run) antes de qualquer TRUNCATE, como a falha de IAM já abortava: a retenção nunca degrada em silêncio para
"sem filtro". PRD e tabela sem regra seguem por `list_rows`, byte-idênticos.

**Onde moram as regras (e por quê).** As cinco regras de 09/09, a constante de 14 dias e
`get_dev_retention_rule` **ficam em `src/config.py`, intocados**. O `config.py` entra no carimbo de
procedência dos 29 serviços (ADR 0001): editá-lo, nem que seja um comentário, deixaria a frota inteira
em deriva até um redeploy completo. O resolvedor novo (`resolve_regra_retencao`) vive em `src/sync/`,
que só o serviço de sync declara; ele devolve as regras de produto, sobrepõe os 14 dias de coleta pelo
número novo (7) e delega o resto ao `config.py`. Limpar o `config.py` (a regra morta do de-vig, o 14)
fica para uma mudança que já exija o redeploy da frota.

**O que NÃO mudou.** PRD segue recebendo 100% das linhas **nesta fatia** (a ADR 0006 revoga isso para as
odds, na #109, reaproveitando o filtro acima). Tabelas pequenas (fixtures, events, standings, h2h, board)
continuam sem corte. A faixa −30/+14 e os 7 dias são os números decididos em 28/09 e **não** foram
validados contra uso real do staging: o Victor foi avisado e pode pedir mais.
