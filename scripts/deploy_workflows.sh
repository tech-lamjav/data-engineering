#!/bin/bash
# Deploy de GCP Workflows (workflow_*.yml na raiz do repo).
#
# Uso:
#   ./scripts/deploy_workflows.sh              # deploya todos
#   ./scripts/deploy_workflows.sh workflow-bets # deploya um
#
# Pre-req: gcloud auth login + GCP_PROJECT_ID no .env

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LOCATION="us-east1"

# nome-do-workflow:arquivo-fonte (relativo ao PROJECT_ROOT)
WORKFLOWS=(
    "workflow-data-engineering:workflow_data_engineering.yml"
    "workflow-injury-report:workflow_injury_report.yml"
    "workflow-bets:workflow_bets.yml"
    "workflow-futebol:workflow_futebol.yml"
    "workflow-futebol-lineups:workflow_futebol_lineups.yml"
    "workflow-futebol-team-stats:workflow_futebol_team_stats.yml"
    "workflow-futebol-odds:workflow_futebol_odds.yml"
)

print_info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

check_gcloud() {
    command -v gcloud &>/dev/null || { print_error "gcloud nao instalado"; exit 1; }
}

check_auth() {
    gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q . \
        || { print_error "Nao autenticado. Rode: gcloud auth login"; exit 1; }
}

load_env() {
    ENV_FILE="$PROJECT_ROOT/.env"
    [ -f "$ENV_FILE" ] || { print_error ".env nao encontrado em $ENV_FILE"; exit 1; }
    set -a; source "$ENV_FILE"; set +a
    [ -n "$GCP_PROJECT_ID" ] || { print_error "GCP_PROJECT_ID nao definido no .env"; exit 1; }
}

get_workflow_source() {
    local name=$1
    for entry in "${WORKFLOWS[@]}"; do
        if [[ "$entry" == "$name:"* ]]; then
            echo "${entry#*:}"
            return 0
        fi
    done
    return 1
}

deploy_workflow() {
    local name=$1
    local source_file=$2
    local source_path="$PROJECT_ROOT/$source_file"

    if [ ! -f "$source_path" ]; then
        print_error "Source nao encontrado: $source_path"
        return 1
    fi

    print_info "Deployando $name a partir de $source_file..."
    # SA explícita: workflows novos defaultariam pra compute SA (sem run.invoker) e
    # dariam 403 ao chamar os serviços Cloud Run. workflowsde@ tem run.invoker + run.admin.
    gcloud workflows deploy "$name" \
        --source="$source_path" \
        --location="$LOCATION" \
        --project="$GCP_PROJECT_ID" \
        --service-account="workflowsde@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
        > /dev/null

    if [ $? -eq 0 ]; then
        print_info "[OK] $name deployado"
        return 0
    else
        print_error "[FAIL] $name"
        return 1
    fi
}

main() {
    check_gcloud
    check_auth
    load_env

    print_info "Projeto: $GCP_PROJECT_ID"
    print_info "Regiao: $LOCATION"
    echo ""

    if [ $# -eq 1 ]; then
        local name=$1
        local source_file
        source_file=$(get_workflow_source "$name") || {
            print_error "Workflow '$name' nao conhecido"
            echo "Disponiveis:"
            for entry in "${WORKFLOWS[@]}"; do echo "  - ${entry%%:*}"; done
            exit 1
        }
        deploy_workflow "$name" "$source_file"
    else
        local ok=0 fail=0
        for entry in "${WORKFLOWS[@]}"; do
            if deploy_workflow "${entry%%:*}" "${entry#*:}"; then
                ok=$((ok + 1))
            else
                fail=$((fail + 1))
            fi
        done
        echo ""
        print_info "Resumo: $ok ok, $fail falha"
        [ $fail -eq 0 ]
    fi
}

main "$@"
