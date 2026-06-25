#!/usr/bin/env bash
# Bootstrap dos secrets de API no Secret Manager (companheiro do --set-secrets em
# deploy_cloud_run.sh). Idempotente: cria o secret se não existir, senão adiciona nova
# versão; concede roles/secretmanager.secretAccessor à service account de runtime.
# Rode UMA vez antes do primeiro deploy com a nova versão do deploy_cloud_run.sh
# (e novamente sempre que rotacionar as chaves no .env).
#
# Uso:  bash scripts/setup_secrets.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

[ -f .env ] || { echo "ERRO: .env não encontrado em $PROJECT_ROOT"; exit 1; }
set -a; source .env; set +a

: "${GCP_PROJECT_ID:?defina GCP_PROJECT_ID no .env}"
: "${BALLDONTLIE_KEY:?defina BALLDONTLIE_KEY no .env}"

SA="${SERVICE_ACCOUNT:-ExtractScripts@${GCP_PROJECT_ID}.iam.gserviceaccount.com}"
case "$SA" in *@*) ;; *) SA="${SA}@${GCP_PROJECT_ID}.iam.gserviceaccount.com";; esac
echo "Projeto: $GCP_PROJECT_ID"
echo "Service account (runtime): $SA"
echo ""

create_secret () {  # $1=nome  $2=valor
  local name="$1" value="$2"
  if gcloud secrets describe "$name" --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
    echo "[$name] já existe — adicionando nova versão"
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --project "$GCP_PROJECT_ID" >/dev/null
  else
    echo "[$name] criando"
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- --replication-policy=automatic --project "$GCP_PROJECT_ID" >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor" \
    --project "$GCP_PROJECT_ID" >/dev/null
  echo "[$name] OK (secretAccessor concedido)"
}

create_secret BALLDONTLIE_KEY "$BALLDONTLIE_KEY"
if [ -n "${API_FOOTBALL_KEY:-}" ]; then
  create_secret API_FOOTBALL_KEY "$API_FOOTBALL_KEY"
else
  echo "API_FOOTBALL_KEY ausente no .env — pulando (ok se você só usa NBA)"
fi

echo ""
echo "== Secrets prontos. Pode seguir para o deploy. =="
