-- DE#109 (história 36 da #112): a coluna `regra_versao` do estado de sincronização do futebol.
-- Rodar UMA VEZ em CADA ambiente (DEV e PRD), ANTES de redeployar a imagem do sync, com a
-- credencial de escrita do data-engineering (psycopg com o `.env`; a MCP do Supabase é somente
-- leitura e não serve). É SQL administrativo, como o do detector de atraso: o sync NÃO faz DDL no
-- estado. Sem esta coluna a imagem nova ABORTA o sync antes de qualquer TRUNCATE, com a mensagem
-- que cita este arquivo (fail-closed: nunca degrada em silêncio para "sem versão").
--
-- POR QUE EXISTE. Mudar a lista de mercados servidos ou o corte de 30 dias precisa forçar nova
-- carga de `fact_odds_snapshot` mesmo com o BigQuery inalterado (o skip-if-unchanged compara só o
-- `modified` do BigQuery, e o `force` não é exposto no serviço). A coluna guarda a descrição da
-- regra em vigor na última carga ("cache-serving/r1/mercados=1,4,5,6,8,12/dias=30/fechamento=t15m"
-- em PRD; "dev-coleta/..." em DEV); o sync só pula a tabela se o BigQuery não mudou E a versão
-- gravada é a de agora. Só as odds usam a coluna; as demais tabelas a deixam nula e o SQL delas
-- não a menciona.
--
-- COMO APLICAR (do checkout com o `.env`; ADD COLUMN IF NOT EXISTS é idempotente, e a coluna
-- nula não reescreve a tabela). O `.env` é lido sem imprimir o valor:
--
--   .venv/bin/python3 - <<'PY'
--   import psycopg
--   from dotenv import dotenv_values
--   env = dotenv_values(".env")
--   sql = open("scripts/sql/sync_state_regra_versao.sql", encoding="utf-8").read()
--   for chave in ("SUPABASE_PG_URL_DEV", "SUPABASE_PG_URL_PRD"):
--       with psycopg.connect(env[chave], autocommit=True) as c:
--           c.execute(sql)
--           print(chave, c.execute(
--               "select column_name, data_type from information_schema.columns "
--               "where table_schema = 'futebol' and table_name = '_sync_state' "
--               "and column_name = 'regra_versao'").fetchone())
--   PY
--
-- Rollback: `alter table futebol._sync_state drop column regra_versao;` SÓ DEPOIS de voltar a
-- imagem para a revisão anterior (a imagem nova lê a coluna e aborta sem ela).
--
-- O detector de atraso lê só `table_name` e `last_synced_bq_modified_time`: a coluna nova não o
-- afeta. A tabela já existe nos dois ambientes (o sync a cria na primeira execução).

alter table futebol._sync_state add column if not exists regra_versao text;

comment on column futebol._sync_state.regra_versao is
    'DE#109: descrição da regra de retenção em vigor na última carga da tabela (só fact_odds_snapshot). '
    'Mudou a regra, o sync recarrega mesmo com o BigQuery inalterado.';
