#!/bin/bash
# Digests VIVOS dos dois Cloud Run Jobs dbt (`dbt-futebol`, `dbt-nba`), uma linha cada:
#
#   vivo <job> live <pacote>@sha256:<64 hex>   o que o spec do job aponta AGORA
#   vivo <job> exec <pacote>@sha256:<64 hex>   o que a última execução rodou (se diferente)
#
# Entrada do `scripts/simula_cleanup_ar.py` (passo 2 do infra/README.md) e do backfill da tag
# `live` dos jobs (passo 1). Só leitura.
#
# O spec do job pode guardar a TAG (`...:latest`, como até 11/2026) ou o digest (`...@sha256:`,
# como o `build-and-push.sh` passou a gravar). Tag é resolvida no registry. A última execução
# entra porque é a última imagem que comprovadamente rodou.
#
# `exec` NÃO é `live-prev`. Quando o spec aponta para um índice (multi-arch/attestation), a
# execução mostra o FILHO amd64 desse mesmo índice: digest diferente, mesma imagem (é o caso
# do dbt-futebol em 07/11/2026). Rotular isso de `live-prev` e taguear em cima moveria o
# `live-prev` rolante (o N-1 que o build-and-push.sh mantém) para a própria imagem viva. O
# backfill do infra/README.md só usa uma linha `exec` como `live-prev` quando o pacote ainda
# não tem `live-prev`.
#
# FAIL-CLOSED: digest que não casa `sha256:[0-9a-f]{64}` aborta com 1. Sem isso, um describe
# que voltasse vazio viraria a linha `<pacote>@` (sem digest), o simulador a ignoraria, e o
# resultado seria um verde falso sobre um vivo que ninguém conferiu.
#
# Todo gcloud aqui roda com `</dev/null`: o script é usado dentro de `while read` no runbook, e
# o gcloud consome o stdin do loop (aconteceu em 07/11/2026: só o 1º job foi processado).

set -euo pipefail

GCP_PROJECT_ID="${GCP_PROJECT_ID:-smartbetting-dados}"
GCP_REGION="${GCP_REGION:-us-east1}"
JOBS=("$@")
[ "${#JOBS[@]}" -eq 0 ] && JOBS=(dbt-futebol dbt-nba)

DIGEST_RE='^[a-z0-9-]+-docker\.pkg\.dev/[^@[:space:]]+@sha256:[0-9a-f]{64}$'

for job in "${JOBS[@]}"; do
    spec=$(gcloud run jobs describe "$job" --region="$GCP_REGION" --project="$GCP_PROJECT_ID" \
        --format='value(spec.template.spec.template.spec.containers[0].image)' </dev/null)
    pacote="${spec%@*}"
    pacote="${pacote%:*}"
    case "$spec" in
        *@sha256:*) atual="$spec" ;;
        *)
            d=$(gcloud artifacts docker images describe "$spec" --project="$GCP_PROJECT_ID" \
                --format='value(image_summary.digest)' </dev/null)
            atual="${pacote}@${d}"
            ;;
    esac
    if ! [[ "$atual" =~ $DIGEST_RE ]]; then
        echo "ERRO: $job: digest do spec ilegível ('$spec' → '$atual')" >&2
        exit 1
    fi
    echo "vivo $job live $atual"

    exec_img=$(gcloud run jobs executions list --job="$job" --region="$GCP_REGION" \
        --project="$GCP_PROJECT_ID" --limit=1 \
        --format='value(spec.template.spec.containers[0].image)' </dev/null)
    if [ -z "$exec_img" ]; then
        continue  # job que nunca rodou: só o spec é vivo
    fi
    if ! [[ "$exec_img" =~ $DIGEST_RE ]]; then
        echo "ERRO: $job: imagem da última execução sem digest legível: '$exec_img'" >&2
        exit 1
    fi
    if [ "$exec_img" != "$atual" ]; then
        echo "vivo $job exec $exec_img"
    fi
done
