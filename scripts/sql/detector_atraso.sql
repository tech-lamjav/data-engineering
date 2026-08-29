-- Pré-requisito do detector de atraso do sync (docs/adr/0002). Rodar UMA VEZ em cada
-- ambiente (PRD e DEV) ANTES de o workflow entrar no master, com um papel administrativo.
--
-- POR QUE ISTO NÃO É AUTO-CRIADO como o `_sync_state` faz em src/sync/bq_to_postgres.py:
-- o detector conecta como `detector_atraso`, um papel que só enxerga duas tabelas e não
-- tem permissão de CREATE. Auto-criar exigiria dar DDL ao papel — o oposto do motivo pelo
-- qual ele existe, já que a credencial vira secret de um repositório PÚBLICO.
--
-- A senha é deixada como placeholder de propósito: gerar uma aqui e commitá-la seria
-- publicá-la. Substituir por uma senha forte, e guardar a URL resultante como os secrets
-- SUPABASE_PG_URL_PRD_RO / SUPABASE_PG_URL_DEV_RO (porta 5432, NUNCA 6543 — o pgbouncer
-- em modo transaction não serve para o sync e não vale divergir aqui).
--
-- Usuário no pooler do Supabase: `detector_atraso.<project_ref>`, no mesmo formato do
-- `postgres.<project_ref>` que o sync usa.

begin;

create table if not exists futebol._detector_state (
    detector        text primary key,
    estado          text not null check (estado in ('verde', 'vermelho')),
    -- início do episódio corrente; é o que o e-mail de recuperação usa p/ dizer a duração
    desde           timestamptz,
    -- último aviso enviado; governa o lembrete de 24h
    ultimo_aviso_em timestamptz
);

-- Rodar só na primeira vez. Para rotacionar a senha depois:
--   alter role detector_atraso with password 'NOVA_SENHA';
create role detector_atraso with login password 'TROCAR_POR_SENHA_FORTE';

grant usage on schema futebol to detector_atraso;

-- Leitura do estado do sync: é daqui que sai o `last_synced_bq_modified_time`.
grant select on futebol._sync_state to detector_atraso;

-- A única tabela em que o detector escreve.
grant select, insert, update on futebol._detector_state to detector_atraso;

commit;

-- Conferência (deve devolver 4 privilégios: 1 em _sync_state, 3 em _detector_state):
--   select table_name, privilege_type from information_schema.table_privileges
--   where grantee = 'detector_atraso' and table_schema = 'futebol';
--
-- E que o papel enxerga o que precisa:
--   set role detector_atraso;
--   select count(*) from futebol._sync_state;   -- deve devolver 22
--   reset role;
