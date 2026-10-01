-- DE#106: o job 12 do pg_cron (`purge-old-snapshots`) do Supabase DEV passa a limpar SÓ o
-- `cron.job_run_details`. Rodar UMA VEZ, no DEV, com a credencial de escrita do data-engineering
-- (psycopg com SUPABASE_PG_URL_DEV do .env; a MCP do Supabase é somente leitura e não serve).
-- NÃO rodar em PRD: o PRD nunca teve esse job (decisão de 08/09/2026 de não tocar produção).
--
-- COMO APLICAR (do checkout com o `.env`, `autocommit=True`; o arquivo já traz begin/commit, e com
-- `autocommit=False` o psycopg só avisaria "there is already a transaction in progress"):
--
--   .venv/bin/python3 - <<'PY'
--   import psycopg
--   from dotenv import dotenv_values
--   url = dotenv_values(".env")["SUPABASE_PG_URL_DEV"]
--   sql = open("scripts/sql/job12_purge_so_job_run_details.sql", encoding="utf-8").read()
--   with psycopg.connect(url, autocommit=True) as c:
--       c.execute(sql)
--       print(c.execute("select jobid, jobname, schedule, active, command "
--                       "from cron.job where jobid = 12").fetchone())
--   PY
--
-- Sai com "job 12 nao e o purge-old-snapshots" (e nada alterado) se o job 12 for outro. O
-- `.env` é lido sem imprimir o valor.
--
-- POR QUE O JOB DEIXA DE APAGAR AS TABELAS DE FUTEBOL: os três DELETE de futebol repetiam cortes
-- que o próprio sync já aplica (TRUNCATE + COPY com a retenção de DEV, ADR 0003), então a limpeza
-- não liberava nada. E ela nunca foi rede de segurança: se a retenção do sync sumisse, o sync
-- recopiaria tudo a cada mudança no BigQuery e o job apagaria de novo, num ciclo. A proteção real
-- do DEV passa a ser o alerta de tamanho do resumo diário (acima de 450 MB; ver o verbete Teto do DEV). Ver a emenda
-- da ADR 0003 e o verbete **Retenção** do CONTEXT.md.
--
-- A parte útil do job é a última linha (o `cron.job_run_details` cresce sem teto, ~16 MB de lixo
-- em 08/09). Ela fica. O número do job (12) e o horário (04:00 UTC) não mudam: a ADR e as notas
-- que citam "job 12" continuam certas. `cron.alter_job` altera no lugar.
--
-- Estado ANTES (lido do DEV em 2026-10-01, somente leitura), para reverter se preciso:
--   jobid=12  jobname='purge-old-snapshots'  schedule='0 4 * * *'  active=true
--   command:
--     DELETE FROM futebol.fact_odds_snapshot WHERE collection_timestamp < now() - interval '14 days';
--     DELETE FROM futebol.fact_injuries_snapshot WHERE snapshot_date < current_date - 14;
--     DELETE FROM futebol.int_futebol_odds_devig d
--       USING futebol.fact_fixtures f
--       WHERE d.fixture_id = f.fixture_id AND f.kickoff_utc < now() - interval '14 days';
--     DELETE FROM cron.job_run_details WHERE start_time < now() - interval '3 days';
--
-- Obs.: o DELETE do de-vig acima podava a cópia congelada de `int_futebol_odds_devig` (a tabela
-- saiu do sync na fatia 0 da DE#112). Sem ele a cópia fica do tamanho que está até o `DROP`
-- (DDL do app, ticket à parte).

begin;

-- Trava de segurança: só altera se o job 12 for mesmo o purge (numeração de job é por banco).
do $$
begin
    if not exists (
        select 1 from cron.job where jobid = 12 and jobname = 'purge-old-snapshots'
    ) then
        raise exception 'job 12 nao e o purge-old-snapshots neste banco: nada foi alterado';
    end if;
end
$$;

select cron.alter_job(
    job_id  => 12,
    command => $job$
      DELETE FROM cron.job_run_details WHERE start_time < now() - interval '3 days';
    $job$
);

commit;

-- Conferência (somente leitura): o comando tem de ser só a limpeza do cron.
select jobid, jobname, schedule, active, command from cron.job where jobid = 12;
