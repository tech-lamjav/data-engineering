---
name: deploy-gcp
description: Deployar qualquer coisa deste repo no GCP — extractor/serviço Cloud Run, Workflow (workflow_*.yml) ou Cloud Scheduler. Use SEMPRE que editar algo em cloud_run/, src/, ou um workflow_*.yml e precisar que valha em produção; ao criar extractor novo; ao mudar memória/timeout/secret de um serviço; ou ao investigar 403 em workflow, PARTIAL_FAILURE, "o scheduler dispara mas nada acontece", ou "deployei e não mudou nada". NUNCA rode gcloud run deploy ou gcloud workflows deploy na mão — leia esta skill antes.
---

# Deploy no GCP (data-engineering)

Projeto `smartbetting-dados`, região **`us-east1`** em tudo (Cloud Run, Workflows,
Scheduler, Artifact Registry). Nunca deploye em outra região.

## Regra número 1: use os scripts, não o gcloud cru

```
Scheduler ──(schedulerde@)──► Workflow ──(workflowsde@)──► Cloud Run ──(ExtractScripts@)──► GCS/BQ
             workflows.invoker            run.invoker          runtime
```

Cada seta é uma **service account diferente**. Os scripts já codificam a certa;
`gcloud run deploy` / `gcloud workflows deploy` na mão **não**, e o erro é silencioso.

| Superfície | Comando | SA aplicada |
|---|---|---|
| Serviço Cloud Run | `./scripts/deploy_cloud_run.sh [sport] [service]` | `ExtractScripts@` (runtime) |
| Workflow | `./scripts/deploy_workflows.sh <workflow-name>` | `workflowsde@` (via `--service-account`) |
| Scheduler | `gcloud scheduler jobs create/update http …` (**sem script**) | `schedulerde@` (você passa na mão) |

> ⚠️ **Workflow deployado sem `--service-account` cai na compute SA default**, que não
> tem `run.invoker`. Resultado: 403 ao chamar o Cloud Run, que o Workflow engole e
> devolve como **`PARTIAL_FAILURE`** — ou até `SUCCEEDED`. É a falha mais cara aqui
> porque parece sucesso. O `deploy_workflows.sh` resolve isso; deploy manual não.

> ⚠️ **Scheduler não tem script.** Ao criar um job novo, passe
> `--oauth-service-account-email=schedulerde@smartbetting-dados.iam.gserviceaccount.com`.
> Com `ExtractScripts@` dá **403 silencioso** — o job aparece como criado e nunca dispara nada.
> Confira os 11 jobs existentes como modelo:
> `gcloud scheduler jobs list --location=us-east1 --format="value(name,httpTarget.oauthToken.serviceAccountEmail)"`

## Pré-voo (30 segundos, evita a maioria dos incidentes)

```bash
gcloud config get-value account   # precisa ser tecnologia@smartbetting.app
```

Há mais de uma conta nesta máquina. Com a pessoal, os deploys dão 403 — ou pior,
sucesso parcial em recursos errados. Os scripts também exigem `.env` na raiz do repo
com `GCP_PROJECT_ID` (eles abortam se faltar).

## Deploy de serviço Cloud Run

```bash
./scripts/deploy_cloud_run.sh                    # tudo (raramente o que você quer)
./scripts/deploy_cloud_run.sh futebol            # todos os extractors de futebol
./scripts/deploy_cloud_run.sh futebol extract-odds   # um só, validado pelo esporte
./scripts/deploy_cloud_run.sh extract-odds       # um só (back-compat)
```

Deploy é **from source** (`--source`), buildado pelo Cloud Build — não há imagem
Docker gerenciada por você aqui (diferente do `dbt-futebol`, que é imagem; para dbt
veja a skill `deploy-dbt-changes` no repo `analytics-engineering`).

Defaults: `512Mi` / `1 CPU` / timeout `3600` / `--no-allow-unauthenticated`.

**Três serviços têm override hardcoded no script** — se precisar mexer em memória,
timeout ou secret deles, edite `scripts/deploy_cloud_run.sh`, não a linha de comando:

- `sync-bq-to-postgres` — `2Gi`, timeout `900`, `--max-instances 1`, Python 3.13 pinado,
  secrets `SUPABASE_PG_URL_PRD/DEV`. O 2Gi é cicatriz de OOM real (10–12/07/2026): o RSS
  acumula entre os requests PRD e DEV na mesma instância. **Não reduza.**
- `notify-execution` — secrets do Gmail via Secret Manager.
- `daily-summary` — lê Cloud Logging + Workflow Executions; a SA precisa de
  `logging.viewer` + `workflows.viewer`.

**Serviço novo?** Adicione ao array certo (`NBA_SERVICES`, `FUTEBOL_SERVICES` ou
`SHARED_SERVICES`) no formato `nome-do-servico:diretorio_em_cloud_run`. O entry point
é derivado do nome (hífen → underscore). Sem isso o script não o conhece.

> `cloud_run/extract_player_props/` é **órfão** de propósito (substituído pelas
> variantes por vendor). Não adicione a `NBA_SERVICES` sem antes checar se não há
> serviço defasado rodando em produção.

## Deploy de Workflow

```bash
./scripts/deploy_workflows.sh                        # todos os 11
./scripts/deploy_workflows.sh workflow-futebol-odds  # um
```

> ⚠️ **Editar `workflow_*.yml` não muda nada em produção.** O Scheduler dispara a
> versão *deployada*, não o arquivo local. Sem rodar o script, sua mudança é invisível
> — e, de novo, sem erro nenhum. Vale para qualquer edição: `--select` do dbt, gate,
> retry, ordem das fases.

Workflow novo precisa ser adicionado ao array `WORKFLOWS` do script
(`nome-do-workflow:arquivo.yml`) **e** ganhar um Scheduler próprio (passo manual acima).

## Verificação — o exit code mente

```bash
# execuções recentes e o estado REAL
gcloud workflows executions list workflow-futebol-odds --location=us-east1 --limit=3
gcloud run services describe <servico> --region=us-east1 --format="value(status.latestReadyRevisionName)"
```

> ⚠️ Workflow **retorna `SUCCEEDED` engolindo `PARTIAL_FAILURE`** de fase interna, e
> `gcloud run jobs execute` **retorna exit 0 mesmo com erro do dbt**. Nenhum exit code
> aqui prova sucesso — confirme pelo log ou pelo dado que deveria ter mudado (linha
> nova no BQ, arquivo novo no GCS).

Depois de um deploy que mexe em coleta, confirme o arquivo no GCS:
**o bucket é `smartbetting-landingzone`** (vem do `.env`). O default do
`src/config.py:437` é `smartbetting-landing`, um bucket **morto** que dá 403 de billing
— se algum comando seu cair nele, é porque o `.env` não foi carregado.

## Checklist

- [ ] `gcloud config get-value account` = `tecnologia@smartbetting.app`
- [ ] Serviço mudou → `deploy_cloud_run.sh` (serviço novo: array atualizado antes)
- [ ] YAML mudou → `deploy_workflows.sh <nome>` (**sempre**, sem exceção)
- [ ] Scheduler novo → criado com `--oauth-service-account-email=schedulerde@…`
- [ ] Verificado por **execução/dado**, não por exit code
- [ ] Commit no repo `data-engineering` (é repo git próprio; a raiz do monorepo não é)

## Mapa completo

Inventário de serviços, workflows, schedulers e SAs:
`docs/ARQUITETURA_DATA_ENGINEERING.md` (a tabela de schedulers está em ~L400).
Consulte de lá em vez de decorar — esta skill é o *procedimento*, aquele doc é o *mapa*.
