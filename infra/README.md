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
  tráfego (`status.traffic` → `status.imageDigest`) depois de todo deploy. O `live-prev` vai
  para a segunda revisão com tráfego (split) ou, sem split, para o digest que tinha `live`
  antes (rolante: o N-1). Nomes fixos: um nome por revisão viraria Keep eterno.
- **Jobs dbt:** `analytics-engineering/build-and-push.sh` empurra só `git-<sha>`, faz
  `jobs update --image <pacote>@sha256:<digest>` e só depois move `live-prev` (para a `live`
  antiga), `live` e `latest`.

A policy só é segura **depois** que a `live` existe em todo digest vivo. Daí a ordem abaixo.

## Estado (07/11/2026)

| Passo | Estado |
|---|---|
| 1. Backfill da `live` | **feito**: `--so-tag-live` 29/29; jobs dbt tagueados à mão (`dbt-futebol` live = índice `d6a25b5b…`, live-prev = `935cac16…`; `dbt-nba` live = índice `200491f9…`, live-prev = `9a2a8cd9…`) |
| 2. Simulação | **feita**, verde nos 3 repos |
| 3. Policies | **aplicadas** sem dry-run e conferidas com o JSON. `keepCount` é por pacote; com no máximo 8 versões por pacote hoje, nada é apagado ainda |
| 4. Lifecycle do bucket de sources | **pendente** |

Os passos abaixo ficam como procedimento para reaplicar uma policy alterada ou recomeçar do
zero. Mudou a policy? Edite o JSON, refaça o passo 2 e reaplique o passo 3.

## Pendências HUMANAS (antes de religar o pipeline)

O pipeline está pausado desde 06/10. Antes de religar:

1. **Fixar os 2 jobs dbt por digest.** O spec deles ainda aponta para `:latest`. O
   `build-and-push.sh` já fixa por digest a cada build, mas os jobs não foram rebuildados
   desde a mudança. Enquanto apontarem para a tag, um `docker push :latest` avulso muda o que
   o job roda sem passar pela `live`. Use os digests que já têm a tag `live`:

   ```bash
   gcloud run jobs update dbt-futebol --region=us-east1 --project=smartbetting-dados \
     --image=us-east1-docker.pkg.dev/smartbetting-dados/dbt-futebol-repo/dbt-futebol@sha256:d6a25b5b76adae9e13cffe794417ddcbfe5c80b3fb1bb3445cfa9b68870aed86
   gcloud run jobs update dbt-nba --region=us-east1 --project=smartbetting-dados \
     --image=us-east1-docker.pkg.dev/smartbetting-dados/dbt-nba-repo/dbt-nba@sha256:200491f9c51387af9bc5a0daddc6a3c19e0289c0b48c5369bca72ca128536593
   ./scripts/vivos_jobs_dbt.sh   # conferir: as linhas "live" mostram esses dois digests
   ```

2. **Taguear o índice pai do `live-prev` do `dbt-nba`.** O `live-prev` (`9a2a8cd9…`, a
   imagem da última execução) é filho do índice `2230481319…`, que não tem tag. A doc do AR é
   ambígua sobre a cascata ("If the parent manifest is deleted, then any related images are
   deleted when the cleanup policy is next run"), então o filho tagueado pode cair junto quando
   o pai sair das 9 versões mais novas. A simulação acusa esse caso (`filho de índice que seria
   APAGADO`).

   ```bash
   PKG=us-east1-docker.pkg.dev/smartbetting-dados/dbt-nba-repo/dbt-nba
   gcloud artifacts docker tags add \
     "$PKG@sha256:2230481319c8dd0482fd09d2041095d73591428ea83958a01232664b836b8680" "$PKG:live-prev-idx"
   ```

   O nome casa com o Keep `live*` da policy. Ela fica fixa até o `dbt-nba` ser rebuildado:
   o build sem attestation não gera mais índice, e aí a tag pode ser removida.

3. **Apagar o pacote órfão `extract-player-props`.** Ele tem 3 versões (~240 MB, de
   12/01/2026). O serviço não existe (`gcloud run services describe extract-player-props` →
   `Cannot find service`), e o "12 mais novas" é por pacote, então a policy nunca limpa um
   pacote com menos de 12 versões. A deleção é permanente: confira antes e apague à mão.

   ```bash
   gcloud run services describe extract-player-props --region=us-east1   # tem de dar "Cannot find service"
   gcloud artifacts packages delete extract-player-props \
     --repository=cloud-run-source-deploy --location=us-east1 --project=smartbetting-dados
   ```

4. **Lifecycle do bucket de sources:** passo 4 abaixo.

## Rollback: só o N-1 é alvo garantido

Cada serviço e cada job tem **um** alvo de rollback protegido por tag: o `live-prev`. Ele é
rolante. O `deploy_cloud_run.sh` move o `live-prev` para o digest que tinha `live` antes de
mover a `live`, e o `build-and-push.sh` faz o mesmo. Do N-2 para trás, só o "12 mais novas"
(serviços) ou o "9 mais novas" (jobs) protege, e 2 ou 3 pushes falhos do `--source` esgotam
essa folga.

Antes de um `update-traffic` (serviço) ou `jobs update --image` (job) para uma versão antiga,
**confirme que o digest ainda existe**:

```bash
gcloud artifacts docker images describe "<pacote>@sha256:<digest>" --format='value(image_summary.digest)'
```

Depois do rollback de um serviço, rode `./scripts/deploy_cloud_run.sh --so-tag-live <servico>`
para a `live` seguir o tráfego.

## Runbook

Pré-voo: `gcloud config get-value account` = `tecnologia@smartbetting.app`. Rode cada bloco
num shell com `set -euo pipefail`: um passo que falha não pode deixar o seguinte rodar sobre
uma lista vazia.

### 1. Backfill da tag `live`

```bash
# 29 serviços: lê o tráfego e aplica live/live-prev, sem deployar nada
./scripts/deploy_cloud_run.sh --so-tag-live
```

Jobs dbt: `scripts/vivos_jobs_dbt.sh` imprime `vivo <job> live <digest>` (o que o spec aponta
agora) e `vivo <job> live-prev <digest>` (a última execução, se diferente). O script falha em
vez de imprimir pacote sem digest.

> ⚠️ **Nunca chame `gcloud` dentro de um `while read` sem `</dev/null`.** O gcloud consome o
> stdin do loop, e o loop para depois da primeira volta. Em 07/11/2026, só o 1º job foi
> tagueado por causa disso. Abaixo, o loop lê pelo descritor 3 e o gcloud recebe `/dev/null`.

```bash
set -euo pipefail
./scripts/vivos_jobs_dbt.sh > /tmp/vivos-dbt.txt
while read -r _ job tag digest <&3; do
  gcloud artifacts docker tags add "$digest" "${digest%@*}:$tag" --quiet </dev/null
done 3< /tmp/vivos-dbt.txt
# Conferência: os 2 jobs com live
for pkg in dbt-futebol-repo/dbt-futebol dbt-nba-repo/dbt-nba; do
  gcloud artifacts docker tags list "us-east1-docker.pkg.dev/smartbetting-dados/$pkg" \
    --format='value(tag)' </dev/null | grep -q '/tags/live$' || { echo "SEM live: $pkg"; exit 1; }
done
echo "2/2 jobs com live"
```

### 2. Simulação local (o dry-run do AR não serve aqui)

O `--dry-run` do Artifact Registry só reporta no audit log de **Data Write**, que está
desligado no projeto: aplicar em dry-run não mostraria nada. A simulação usa o mesmo JSON que
vai ser aplicado e confere a lista de digests vivos. Exits: `0` = nenhum vivo cai; `1` = algum
vivo cai; `2` = entrada insuficiente para responder. Uma lista de vivos vazia, cortada, de outro
repo ou com contagem diferente de `--esperado` dá `2`, nunca verde.

```bash
set -euo pipefail
gcloud artifacts docker images list \
  us-east1-docker.pkg.dev/smartbetting-dados/cloud-run-source-deploy \
  --include-tags --format=json > /tmp/ar-servicos.json
./scripts/deploy_cloud_run.sh --lista-vivos > /tmp/vivos.txt
# --esperado: 29, mais 1 por serviço com traffic split (o live-prev dele também é vivo)
python3 scripts/simula_cleanup_ar.py infra/ar-cleanup-cloud-run-source-deploy.json \
  /tmp/ar-servicos.json /tmp/vivos.txt --esperado 29
```

Os repos dbt têm índices (attestation do buildx até 11/2026). O filho amd64 que o job roda não
tem tag e só fica porque o índice pai fica. `scripts/indices_ar.py` lê os manifestos pela API
de registry e monta o mapa pai → filho, e a simulação aplica a regra do AR com ele. Sem
`--indices`, um listing com índice sai com `2`.

```bash
set -euo pipefail
./scripts/vivos_jobs_dbt.sh > /tmp/vivos-dbt.txt
for repo in dbt-futebol-repo dbt-nba-repo; do
  gcloud artifacts docker images list "us-east1-docker.pkg.dev/smartbetting-dados/$repo" \
    --include-tags --format=json > "/tmp/ar-$repo.json" </dev/null
  python3 scripts/indices_ar.py "/tmp/ar-$repo.json" > "/tmp/indices-$repo.txt"
  # --esperado 2: live + live-prev de cada job (1 se a última execução = spec)
  python3 scripts/simula_cleanup_ar.py infra/ar-cleanup-dbt.json "/tmp/ar-$repo.json" \
    /tmp/vivos-dbt.txt --esperado 2 --indices "/tmp/indices-$repo.txt"
done
```

**Só siga se cada simulação terminar com exit 0.**
- `seria APAGADO`: falta a `live` naquele digest; volte ao passo 1.
- `filho de índice que seria APAGADO`: taguear o pai (ver Pendências, item 2).

Referência (07/11/2026, antes do backfill):

| Repo | Versões | Apaga | Mantém | Digest vivo em risco sem backfill? |
|---|---:|---:|---:|---|
| `cloud-run-source-deploy` | 2.034 | 1.683 | 351 | não (os 29 estão nas 12 mais novas) |
| `dbt-futebol-repo` | 283 | 274 | 9 | não |
| `dbt-nba-repo` | 56 | 47 | 9 | **sim, o da última execução**: o `dbt-nba` está dormente, e a última execução (25/05) rodou um digest de 17/05 que já não estava entre as 9 mais novas. O que o spec aponta (`latest`, 17/08) está protegido. |

### 3. Aplicar as policies

```bash
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --project=smartbetting-dados --location=us-east1 \
  --policy=infra/ar-cleanup-cloud-run-source-deploy.json --no-dry-run

for repo in dbt-futebol-repo dbt-nba-repo; do
  gcloud artifacts repositories set-cleanup-policies "$repo" \
    --project=smartbetting-dados --location=us-east1 \
    --policy=infra/ar-cleanup-dbt.json --no-dry-run </dev/null
done
```

A cleanup roda em background e leva até ~1 dia para agir. Conferência no dia seguinte: refaça
o `images list` do passo 2 e compare a contagem com a simulação.

### 4. Lifecycle do bucket de sources do `--source`

O `gcloud run deploy --source` sobe o tarball do diretório para
`gs://run-sources-smartbetting-dados-us-east1` a cada deploy; depois do build ninguém lê. O
bucket não tinha regra de lifecycle em 07/11/2026, então o arquivo não sobrescreve nada.

```bash
gcloud storage buckets update gs://run-sources-smartbetting-dados-us-east1 \
  --lifecycle-file=infra/gcs-lifecycle-run-sources.json
```

## Rotina

- Deploy de serviço: `./scripts/deploy_cloud_run.sh --derivados [sport]` deploya só o que
  derivou do carimbo (cada deploy desnecessário são ~4 versões no registry). A tag `live` sai do
  próprio deploy; se ela falhar, o deploy sai com erro e manda rodar `--so-tag-live <servico>`.
- Rollback: ver a seção acima. Só o N-1 (`live-prev`) é alvo garantido.
