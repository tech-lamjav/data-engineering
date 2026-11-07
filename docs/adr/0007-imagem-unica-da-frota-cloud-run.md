# Imagem única para a frota de 29 serviços Cloud Run (`--image` em vez de `--source`)

**Status:** proposed (2026-11-07). É só desenho: nada aqui está implementado. O que já entrou
(tag `live`, `--derivados`, cleanup policies versionadas) é paliativo e vale com ou sem este ADR.
**Relacionado:** ADR 0001 (carimbo de procedência), `infra/README.md` (cleanup do Artifact Registry)

## Contexto

Auditoria de custo de 07/11/2026: R$ 44,92 nos últimos 30 dias, dos quais 83% (R$ 37) é
armazenamento do Artifact Registry. O repo `cloud-run-source-deploy` tinha 61,9 GiB em 2.034
versões; só setembro somou +23 GB.

A causa é o `gcloud run deploy --source` (buildpacks, sem Dockerfile) que o
`scripts/deploy_cloud_run.sh` roda 29 vezes. Cada serviço é buildado do zero no Cloud Build, e
cada build sobe ~4 versões e uma camada nova de ~127 MB de runtime Python, embora os 29 tenham a
mesma árvore `src/` + `scripts/`, praticamente as mesmas dependências e só difiram no `main.py`.
Um commit no núcleo (`src/config.py`, `src/clients/`, …) deriva os 29 juntos (~a cada 2,4 dias,
ADR 0001), e o deploy completo leva ~60 minutos e 29 builds.

A cleanup policy (`infra/ar-cleanup-cloud-run-source-deploy.json`) e o `--derivados` reduzem o
estoque e o fluxo, mas não mudam a unidade de build: uma mudança no núcleo continua custando 29
builds e ~116 versões.

## Decisão proposta

Uma imagem só, buildada uma vez por commit a partir de um `Dockerfile` na raiz do repo, e os 29
serviços deployados com `gcloud run deploy --image <pacote>@sha256:<digest>`. O que hoje
diferencia os serviços no build passa a ser configuração de runtime:

| Hoje (por serviço, no build) | Proposto (por serviço, no deploy) |
|---|---|
| `main.py` copiado para a raiz do diretório temporário | `FUNCTION_SOURCE=/app/cloud_run/<dir>/main.py` |
| `--set-build-env-vars GOOGLE_FUNCTION_TARGET=<entry>` | `FUNCTION_TARGET=<entry>` em `--set-env-vars` |
| `Procfile` (só `notify-execution` e `daily-summary`) | `CMD functions-framework --source=$FUNCTION_SOURCE --target=$FUNCTION_TARGET --port=$PORT` no Dockerfile |
| `cloud_run/<dir>/requirements.txt` | `requirements-frota.txt` único (ver 3) |
| runtime Python do buildpack (3.13 pinado só no sync) | `python:3.13-slim` para os 29 |

Memória, CPU, timeout, conta de runtime, secrets e `--max-instances` continuam por serviço nas
mesmas ramificações do `deploy_service()`: nada disso é da imagem.

O build vai para um repo novo, `frota-cloud-run`, com um pacote só (`frota`), tag `git-<sha>` e
`--provenance=false --sbom=false` (mesmo motivo do `build-and-push.sh` dos jobs dbt: uma versão
por build, sem índice de attestation).

### 1. Manifesto de procedência

Hoje o `procedencia_servicos.sh` põe `cloud_run/<dir>/requirements.txt` no **núcleo** de cada
serviço e o `main.py`/`Procfile` na parte **svc**, e `tests/test_procedencia_servicos.py` afirma
os `Procfile`s (ex.: `cloud_run/daily_summary/Procfile`).

Com a imagem única, o núcleo de **todos** os 29 passa a declarar `Dockerfile` e
`requirements-frota.txt` (os dois definem o runtime de todo mundo; mudar qualquer um deriva os 29,
o que é a verdade). A parte svc fica só com o `main.py` e os módulos que ele executa (decisão A′ do
ADR 0001 continua valendo). Os `Procfile`s e os `requirements.txt` por serviço são apagados, e o
teste passa a afirmar o oposto: que nenhum serviço declara `Procfile` nem requirements próprio, e
que o `test_manifesto_fecho_de_imports.py` resolve as dependências externas contra o
`requirements-frota.txt`.

O carimbo continua sendo hash do disco gravado em env var (ADR 0001); o digest da imagem é uma
segunda identidade, legível em `status.imageDigest` da revisão, mas não substitui o carimbo (um
build sujo de worktree tem digest novo e carimbo que acusa a sujeira; só o carimbo diz *qual*
parte divergiu).

### 2. `main.py` supõe `src/` e `scripts/` como irmãos

Os 29 `main.py` fazem `sys.path.insert(0, dirname(__file__))` e inserem `scripts/` (e
`scripts/futebol/` nos 13 de futebol), supondo que o deploy copiou `src/` e
`scripts/` para o lado deles. Na imagem única o `main.py` mora em `/app/cloud_run/<dir>/` e não tem
irmãos.

Escolha: **`PYTHONPATH=/app:/app/scripts:/app/scripts/futebol` no Dockerfile, sem editar os 29**.
Os `sys.path.insert` que já existem viram inofensivos (apontam para um diretório que só tem o
próprio `main.py`). Editar os 29 foi descartado: deriva os 29 de uma vez por uma mudança que não
altera comportamento, e qualquer `main.py` esquecido quebra só em produção.

Risco do PYTHONPATH global: hoje cada serviço vê `scripts/futebol/` antes de `scripts/` só se for
de futebol; com o caminho global, um módulo de mesmo nome nos dois diretórios resolveria igual para
todos. Em 07/11/2026 **não há nenhum nome repetido** entre `scripts/*.py` e
`scripts/futebol/*.py`. A implementação precisa de um teste que mantenha isso verdade (falha se
aparecer nome repetido), ou a regressão chega muda.

### 3. União das dependências

Os `requirements.txt` por serviço hoje são cinco variantes. A união é
`functions-framework==3.10.2`, `google-cloud-storage==2.14.0`, `requests==2.32.3`,
`python-dotenv==1.0.0`, `google-cloud-bigquery==3.27.0`, `psycopg[binary]==3.2.3`,
`google-cloud-logging==3.16.0`, `google-cloud-workflows==1.22.0`, `tzdata==2025.3`, mais o
`protobuf>=5.28.0` do `requirements.txt` da raiz. Não há pin conflitante entre elas, e o CI
(`testes.yml`) já instala todas no mesmo interpretador para o auditor de fecho de imports, o que é
evidência parcial de que convivem.

Duas coisas mudam de verdade, e por isso o rollout é em canário (abaixo):

- **Python 3.13 para os 28 que hoje usam o default do buildpack.** O sync está pinado em 3.13
  porque `psycopg[binary]==3.2.3` não tem wheel cp314; a imagem única herda o pin de todo mundo.
- **Transitivas resolvidas uma vez para todos.** `google-api-core`/`protobuf` saem da mesma
  resolução para os 29. O Dockerfile roda `pip check` no build e falha se a resolução for
  inconsistente.

O custo de cada serviço carregar o psycopg e os clientes de Logging/Workflows que não usa são
dezenas de MB numa imagem só, contra 29 imagens de ~227 MB hoje.

### 4. Onde mora o cache de build

No próprio Artifact Registry, sem repo de cache separado. O build roda no Cloud Build
(`gcloud builds submit`, não na máquina do operador: o macOS ARM buildaria `linux/amd64` por
emulação) com `docker build --cache-from <pacote>:latest`, e o Dockerfile ordena as camadas do
mais estável para o mais volátil: base Python → `requirements-frota.txt` → `src/` → `scripts/` →
`cloud_run/`. Um commit que só mexe em `src/` reaproveita as camadas de runtime e dependências e
sobe só a camada de código (~1 MB). A imagem anterior já é o cache, então a cleanup policy do repo
novo não precisa conhecer nenhum artefato de cache. Kaniko com `--cache-repo` foi descartado: cria
um repo de camadas que precisa de policy própria e é mais uma coisa para apodrecer.

Policy do repo `frota-cloud-run`: Keep `tagPrefixes:[live]`, Keep `mostRecentVersions:5`, Delete
`olderThan:30d`. Com várias revisões vivas em digests diferentes (ver `--derivados` abaixo), a tag
`live` vira **uma por serviço**: `live-<servico>` (29 nomes fixos, não um por revisão, então o
Keep por prefixo continua limitado). O `marcar_live()` do `deploy_cloud_run.sh` muda o nome da tag
e o pacote passa a ser o mesmo para todos; a leitura do tráfego não muda.

`--derivados` continua útil: deployar só os serviços que derivaram cria menos revisões e menos
risco, e o build já não é o custo. A consequência é que serviços diferentes rodam digests
diferentes da mesma imagem, por isso a tag por serviço.

### 5. Golden test

`tests/test_deploy_cloud_run_argv.py` congela o argv dos 29 `gcloud run deploy`. A troca muda o
argv de propósito, e o golden é regenerado (`python3 tests/test_deploy_cloud_run_argv.py --update`)
no mesmo PR, com o diff revisado linha a linha. O diff esperado, e só ele:

- `--source <TEMP_DIR>` vira `--image <pacote>@sha256:<digest do stub>`;
- `--set-build-env-vars` some dos 28 que o têm;
- `--set-env-vars` ganha `FUNCTION_SOURCE` e `FUNCTION_TARGET` e perde `GOOGLE_FUNCTION_TARGET`.

O stub do `gcloud` passa a responder `builds submit` e `artifacts docker images describe` (digest
determinístico), e o teste ganha uma asserção nova: **um** build para os 29 deploys. Qualquer outra
linha diferente no diff do golden é regressão, não efeito da troca.

## Rollout

1. PR com Dockerfile, `requirements-frota.txt`, manifesto e golden; `deploy_cloud_run.sh` ganha
   `--image` atrás de uma variável (`DEPLOY_MODO=imagem`), `--source` continua o default.
2. Canário: `extract-leagues` (baixa frequência, idempotente) com a imagem; conferir uma execução
   real do workflow e o carimbo. Depois `notify-execution` (testa o caminho sem Procfile) e o
   `sync-bq-to-postgres` (2Gi, Python já em 3.13).
3. Os 26 restantes; depois `--source` sai do script.
4. Com nenhum serviço servindo digest do `cloud-run-source-deploy` (`--lista-vivos`), o repo e o
   bucket `run-sources-*` podem ser esvaziados.

Rollback em qualquer passo: redeploy do serviço com `--source` (passos 1-2) ou `update-traffic`
para a revisão anterior (o digest dela está protegido por `live-prev`/keep 5).

## Consequências

- Mudança no núcleo: 1 build (~1 MB de camada nova) em vez de 29 builds e ~116 versões. Deploy
  completo deixa de ser ~60 min de Cloud Build.
- O repo `cloud-run-source-deploy` e o bucket de sources do `--source` deixam de crescer.
- Os 28 serviços mudam de runtime Python (para 3.13) na troca: é o principal risco, e o motivo do
  canário.
- Dockerfile e `requirements-frota.txt` entram no núcleo do carimbo: bump de dependência deriva os
  29, e isso é correto.

## Alternativas consideradas

**Ficar no `--source` com cleanup policy.** É o que existe hoje (paliativo). Resolve o estoque, não
o fluxo: cada mudança no núcleo ainda custa 29 builds.

**Uma imagem por serviço com Dockerfile próprio.** Cache melhor que o buildpack, mas 29
Dockerfiles para manter iguais, e as 29 imagens continuam a ser buildadas e guardadas.

**Editar os 29 `main.py` para não depender de irmãos.** Ver 2: deriva os 29 sem mudar
comportamento e só quebra em produção se um ficar para trás.
