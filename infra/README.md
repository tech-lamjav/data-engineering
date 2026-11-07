# infra/: retenção do Artifact Registry e do bucket de sources

Configuração de GCP versionada aqui, **aplicada à mão por um humano**, na ordem do runbook
abaixo. Nenhum script deste repo aplica estes arquivos.

| Arquivo | Aplica em | O que faz |
|---|---|---|
| `ar-cleanup-cloud-run-source-deploy.json` | repo AR `cloud-run-source-deploy` (29 serviços) | Keep tag `live*`; Keep 12 versões mais novas **por pacote**; Delete > 30 dias |
| `ar-cleanup-dbt.json` | repos AR `dbt-futebol-repo` e `dbt-nba-repo` | Keep tags `live*` e `latest*`; Keep 9 mais novas; Delete > 30 dias |
| `gcs-lifecycle-run-sources.json` | bucket `gs://run-sources-smartbetting-dados-us-east1` | apaga objeto com mais de 30 dias |

## Por que

Auditoria de 07/11/2026: R$ 44,92 em 30 dias, 83% armazenamento do Artifact Registry. O repo
`cloud-run-source-deploy` tinha 61,9 GiB em 2.034 versões e nenhum repo tinha cleanup policy.
Cada `gcloud run deploy --source` sobe ~4 versões (por isso o keep **12** = ~3 deploys de
rollback por serviço). A troca estrutural (uma imagem para a frota) está desenhada no
`docs/adr/0007-imagem-unica-da-frota-cloud-run.md`.

## O que protege o digest vivo

Os serviços escalam a zero: o cold start puxa a imagem do registry. **Digest vivo apagado =
serviço que não sobe.** A tag `latest` NÃO protege: o `deploy --source` move a `latest` no
push, antes de a revisão ficar pronta (5 pushes medidos nunca viraram revisão). Por isso:

- **Serviços:** `scripts/deploy_cloud_run.sh` aplica `live` no digest da revisão com mais
  tráfego (`status.traffic` → `status.imageDigest`) depois de todo deploy, e `live-prev` numa
  segunda revisão com tráfego (split). Nomes fixos: um nome por revisão viraria Keep eterno.
- **Jobs dbt:** `analytics-engineering/build-and-push.sh` faz `jobs update --image
  <pacote>@sha256:<digest>` e só depois move `live` para esse digest.

A policy só é segura **depois** que a `live` existe em todo digest vivo. Daí a ordem abaixo.

## Runbook (uma vez)

Pré-voo: `gcloud config get-value account` = `tecnologia@smartbetting.app`, e o PR com a tag
`live` mergeado no `data-engineering` e no `analytics-engineering`.

### 1. Backfill da tag `live`

```bash
# 29 serviços: lê o tráfego e aplica live/live-prev, sem deployar nada
./scripts/deploy_cloud_run.sh --so-tag-live
```

Jobs dbt: antes deste PR o spec do job guarda a TAG (`...:latest`), sem digest resolvido em
lugar nenhum do job. A próxima execução roda o que o spec aponta (hoje, o `latest`); a última
execução mostra o digest que rodou da última vez, que pode ser outro (o `dbt-nba` está dormente
desde 25/05: rodou um digest de 17/05, e o `latest` é de 17/08). Os dois são protegidos:
`live` no que o spec resolve agora, `live-prev` no da última execução.

```bash
# 2 jobs dbt. A função vivos_dbt é reusada no passo 2.
vivos_dbt() {  # imprime "<job> <atual> <ultima_execucao>"
  local job spec pkg atual exec_img
  for job in dbt-futebol dbt-nba; do
    spec=$(gcloud run jobs describe "$job" --region=us-east1 \
           --format='value(spec.template.spec.template.spec.containers[0].image)')
    pkg=${spec%@*}; pkg=${pkg%:*}
    case "$spec" in
      *@sha256:*) atual="$spec" ;;
      *) atual="$pkg@$(gcloud artifacts docker images describe "$spec" --format='value(image_summary.digest)')" ;;
    esac
    exec_img=$(gcloud run jobs executions list --job="$job" --region=us-east1 --limit=1 \
               --format='value(spec.template.spec.containers[0].image)')
    echo "$job $atual $exec_img"
  done
}
vivos_dbt | while read -r job atual exec_img; do
  pkg=${atual%@*}
  case "$atual" in *@sha256:????????????????????????????????????????????????????????????????) ;;
    *) echo "PARE: $job sem digest legível: '$atual'"; continue ;; esac
  gcloud artifacts docker tags add "$atual" "$pkg:live"
  case "$exec_img" in "$atual"|"") ;; *@sha256:*) gcloud artifacts docker tags add "$exec_img" "$pkg:live-prev" ;; esac
done
```

### 2. Simulação local (o dry-run do AR não serve aqui)

O `--dry-run` do Artifact Registry só reporta no audit log de **Data Write**, que está
desligado no projeto: aplicar em dry-run não mostraria nada. A simulação usa o mesmo JSON que
vai ser aplicado e confere a lista de digests vivos.

```bash
gcloud artifacts docker images list \
  us-east1-docker.pkg.dev/smartbetting-dados/cloud-run-source-deploy \
  --include-tags --format=json > /tmp/ar-servicos.json
./scripts/deploy_cloud_run.sh --lista-vivos > /tmp/vivos.txt
python3 scripts/simula_cleanup_ar.py infra/ar-cleanup-cloud-run-source-deploy.json \
  /tmp/ar-servicos.json /tmp/vivos.txt
```

```bash
for repo in dbt-futebol-repo dbt-nba-repo; do
  gcloud artifacts docker images list "us-east1-docker.pkg.dev/smartbetting-dados/$repo" \
    --include-tags --format=json > "/tmp/ar-$repo.json"
done
vivos_dbt > /tmp/vivos-dbt.txt   # função do passo 1
python3 scripts/simula_cleanup_ar.py infra/ar-cleanup-dbt.json /tmp/ar-dbt-futebol-repo.json /tmp/vivos-dbt.txt
python3 scripts/simula_cleanup_ar.py infra/ar-cleanup-dbt.json /tmp/ar-dbt-nba-repo.json /tmp/vivos-dbt.txt
```

**Só siga se cada simulação terminar com `✓ Nenhum digest vivo seria apagado` (exit 0).**
Exit 1 com `seria APAGADO` = falta a `live` naquele digest: volte ao passo 1.

A simulação é **conservadora**: não modela a regra do AR de não apagar imagem referenciada por
um índice mantido (os repos dbt têm índices de attestation, ver o aviso `é um ÍNDICE`). O
conjunto que ela apaga é um superconjunto do real; as contagens e os GiB são teto. Para a
pergunta de segurança (algum vivo cai?), isso só produz falso alarme, nunca falso verde.

Referência (07/11/2026, antes do backfill):

| Repo | Versões | Apaga | Mantém | Digest vivo em risco sem backfill? |
|---|---:|---:|---:|---|
| `cloud-run-source-deploy` | 2.034 | 1.683 | 351 | não (os 29 estão nas 12 mais novas) |
| `dbt-futebol-repo` | 283 | 274 | 9 | não |
| `dbt-nba-repo` | 56 | 47 | 9 | **sim, o da última execução**: o `dbt-nba` está dormente e rodou pela última vez (25/05) um digest de 17/05 que já não está entre as 9 mais novas. O que o spec aponta hoje (`latest`, 17/08) está protegido. Sem o `live-prev` do passo 1, a imagem da última execução boa some (rollback e forense do NBA). |

### 3. Aplicar as policies

```bash
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --project=smartbetting-dados --location=us-east1 \
  --policy=infra/ar-cleanup-cloud-run-source-deploy.json --no-dry-run

for repo in dbt-futebol-repo dbt-nba-repo; do
  gcloud artifacts repositories set-cleanup-policies "$repo" \
    --project=smartbetting-dados --location=us-east1 \
    --policy=infra/ar-cleanup-dbt.json --no-dry-run
done
```

A cleanup roda em background e leva até ~1 dia para agir. Conferência no dia seguinte: refaça
o `images list` do passo 2 e compare a contagem com a simulação.

### 4. Lifecycle do bucket de sources do `--source`

O `gcloud run deploy --source` sobe o tarball do diretório para
`gs://run-sources-smartbetting-dados-us-east1` a cada deploy; depois do build ninguém lê.

```bash
gcloud storage buckets update gs://run-sources-smartbetting-dados-us-east1 \
  --lifecycle-file=infra/gcs-lifecycle-run-sources.json
```

## Rotina (depois do runbook)

- Deploy de serviço: `./scripts/deploy_cloud_run.sh --derivados [sport]` deploya só o que
  derivou do carimbo (cada deploy desnecessário são ~4 versões no registry). A tag `live` sai do
  próprio deploy; se ela falhar, o deploy sai com erro e manda rodar `--so-tag-live <servico>`.
- Rollback manual (`gcloud run services update-traffic`): o digest antigo está nas 12 mais
  novas do pacote; rode `./scripts/deploy_cloud_run.sh --so-tag-live <servico>` logo depois
  para a `live` seguir o tráfego.
- Policy mudou? Edite o JSON aqui, rode a simulação do passo 2 e reaplique o passo 3.
