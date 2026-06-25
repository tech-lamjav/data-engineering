#!/bin/bash
# Script para fazer deploy de serviços Cloud Run
# Lê variáveis de ambiente do arquivo .env e faz deploy de todos os serviços ou um específico

set -euo pipefail

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Diretório raiz do projeto
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configurações
REGION="us-east1"
MEMORY="512Mi"
CPU="1"
TIMEOUT="3600"  # 60 minutos (máximo permitido pelo Cloud Run para requisições HTTP)

# Listas de serviços por esporte (compatível com bash 3.x)
# Formato: SERVICE_NAME:SERVICE_DIR (SERVICE_DIR relativo a cloud_run/)
NBA_SERVICES=(
    "extract-active-players:extract_active_players"
    "extract-games:extract_games"
    "extract-game-player-stats:extract_game_player_stats"
    "extract-game-player-stats-period:extract_game_player_stats_period"
    "extract-game-player-advanced-stats:extract_game_player_advanced_stats"
    "extract-season-averages:extract_season_averages"
    "extract-team-season-averages:extract_team_season_averages"
    "extract-player-injuries:extract_player_injuries"
    "extract-team-standings:extract_team_standings"
    "extract-player-props-draftkings:extract_player_props_draftkings"
    "extract-player-props-caesars:extract_player_props_caesars"
    "extract-player-props-betrivers:extract_player_props_betrivers"
    "extract-betting-odds:extract_betting_odds"
)
# NOTA: cloud_run/extract_player_props/ é um diretório ÓRFÃO — substituído pelas
# variantes por vendor (-draftkings/-caesars/-betrivers) acima. NÃO está em
# NBA_SERVICES nem é chamado por nenhum workflow (ver workflow_bets.yml /
# workflow_data_engineering.yml), portanto NÃO é deployado por este script.
# O wrapper genérico só sobrevive como exemplo do script local documentado em
# CLAUDE.md (scripts/extract_player_props.py). Não adicione a NBA_SERVICES sem
# antes confirmar que não há serviço Cloud Run órfão rodando defasado em produção.

FUTEBOL_SERVICES=(
    "extract-leagues:futebol/extract_leagues"
    "extract-teams:futebol/extract_teams"
    "extract-players:futebol/extract_players"
    "extract-fixtures:futebol/extract_fixtures"
    "extract-fixture-statistics:futebol/extract_fixture_statistics"
    "extract-fixture-events:futebol/extract_fixture_events"
    "extract-fixture-lineups:futebol/extract_fixture_lineups"
    "extract-fixture-player-stats:futebol/extract_fixture_player_stats"
    "extract-team-season-stats:futebol/extract_team_season_stats"
    "extract-standings:futebol/extract_standings"
    "extract-injuries:futebol/extract_injuries"
    "extract-odds:futebol/extract_odds"
    "extract-predictions:futebol/extract_predictions"
)

# Serviços compartilhados (não específicos de esporte)
SHARED_SERVICES=(
    "notify-execution:notify_execution"
    "sync-bq-to-postgres:sync_bq_to_postgres"
    "daily-summary:daily_summary"
)

# União: todos os serviços (preserva back-compat com `./deploy_cloud_run.sh extract-X`)
SERVICES=(
    "${NBA_SERVICES[@]}"
    "${FUTEBOL_SERVICES[@]}"
    "${SHARED_SERVICES[@]}"
)

# Esportes suportados — usados para arg parsing (./deploy_cloud_run.sh futebol [...])
SUPPORTED_SPORTS=("nba" "futebol")

# Verifica se um valor é um esporte conhecido
is_supported_sport() {
    local candidate=$1
    local sport
    for sport in "${SUPPORTED_SPORTS[@]}"; do
        [ "$sport" = "$candidate" ] && return 0
    done
    return 1
}

# Retorna o array de serviços de um esporte específico (echo na stdout)
get_services_for_sport() {
    local sport=$1
    local arr_name="$(echo "$sport" | tr '[:lower:]' '[:upper:]')_SERVICES[@]"
    echo "${!arr_name}"
}

# Função para obter o diretório do serviço
get_service_dir() {
    local service_name=$1
    local service_entry
    for service_entry in "${SERVICES[@]}"; do
        if [[ "$service_entry" == "$service_name:"* ]]; then
            echo "${service_entry#*:}"
            return 0
        fi
    done
    return 1
}

# Verifica se um serviço pertence a um esporte (uso: pair sport+service no CLI)
service_belongs_to_sport() {
    local service_name=$1
    local sport=$2
    local arr_name="$(echo "$sport" | tr '[:lower:]' '[:upper:]')_SERVICES[@]"
    local entry
    for entry in "${!arr_name}"; do
        [[ "$entry" == "$service_name:"* ]] && return 0
    done
    return 1
}

# Função para obter o entry point do serviço
get_entry_point() {
    local service_name=$1
    # Converte extract-games -> extract_games, extract-active-players -> extract_active_players, etc.
    echo "$service_name" | tr '-' '_'
}

# Função para imprimir mensagens
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Verificar se gcloud está instalado
check_gcloud() {
    if ! command -v gcloud &> /dev/null; then
        print_error "gcloud CLI não está instalado. Instale em: https://cloud.google.com/sdk/docs/install"
        exit 1
    fi
    print_info "gcloud CLI encontrado"
}

# Verificar autenticação
check_auth() {
    if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
        print_error "Você não está autenticado no gcloud. Execute: gcloud auth login"
        exit 1
    fi
    print_info "Autenticação verificada"
}

# Verificar e carregar variáveis de ambiente
load_env() {
    ENV_FILE="$PROJECT_ROOT/.env"
    
    if [ ! -f "$ENV_FILE" ]; then
        print_error "Arquivo .env não encontrado em $ENV_FILE"
        print_info "Crie um arquivo .env na raiz do projeto com as seguintes variáveis:"
        echo "  BALLDONTLIE_KEY=your_api_key"
        echo "  GCS_BUCKET_NAME=smartbetting-landing"
        echo "  GCP_PROJECT_ID=your-project-id"
        echo "  SEASON=2025"
        echo "  LOG_LEVEL=INFO"
        echo "  SERVICE_ACCOUNT=ExtractScripts (opcional, padrão: ExtractScripts)"
        exit 1
    fi
    
    print_info "Carregando variáveis de ambiente de $ENV_FILE"
    
    # Carrega variáveis do .env
    set -a
    source "$ENV_FILE"
    set +a
    
    # Verifica variáveis obrigatórias
    # ${VAR:-} protege contra `set -u` quando a variável não vier do .env.
    if [ -z "${BALLDONTLIE_KEY:-}" ]; then
        print_error "BALLDONTLIE_KEY não encontrada no .env"
        exit 1
    fi

    if [ -z "${GCS_BUCKET_NAME:-}" ]; then
        print_error "GCS_BUCKET_NAME não encontrada no .env"
        exit 1
    fi

    if [ -z "${GCP_PROJECT_ID:-}" ]; then
        print_error "GCP_PROJECT_ID não encontrada no .env"
        exit 1
    fi

    if [ -z "${SEASON:-}" ]; then
        print_warning "SEASON não encontrada no .env, usando padrão: 2025"
        SEASON="2025"
    fi

    if [ -z "${LOG_LEVEL:-}" ]; then
        LOG_LEVEL="INFO"
    fi

    # API_FOOTBALL_KEY é opcional (somente serviços de futebol usam).
    # Warn não-fatal — não quebra deploys NBA-only.
    if [ -z "${API_FOOTBALL_KEY:-}" ]; then
        print_warning "API_FOOTBALL_KEY não encontrada no .env (necessária apenas para serviços de futebol)"
    fi

    # Service account (opcional, padrão: ExtractScripts)
    if [ -z "${SERVICE_ACCOUNT:-}" ]; then
        SERVICE_ACCOUNT="ExtractScripts@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
        print_info "Usando service account padrão: $SERVICE_ACCOUNT"
    else
        # Se não contém @, assume que é apenas o nome
        if [[ "$SERVICE_ACCOUNT" != *"@"* ]]; then
            SERVICE_ACCOUNT="${SERVICE_ACCOUNT}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
        fi
        print_info "Usando service account: $SERVICE_ACCOUNT"
    fi
    
    print_info "Variáveis de ambiente carregadas com sucesso"
}

# Converte variáveis de ambiente para formato --set-env-vars
build_env_vars() {
    # Apenas config NÃO sensível. As chaves de API vão via Secret Manager (build_secrets).
    ENV_VARS="GCS_BUCKET_NAME=${GCS_BUCKET_NAME}"
    ENV_VARS="${ENV_VARS},GCP_PROJECT_ID=${GCP_PROJECT_ID}"
    ENV_VARS="${ENV_VARS},SEASON=${SEASON}"
    ENV_VARS="${ENV_VARS},LOG_LEVEL=${LOG_LEVEL}"
    echo "$ENV_VARS"
}

# Monta o --set-secrets das chaves de API (lidas do Secret Manager em runtime, não em texto puro).
# Pré-requisito: os secrets BALLDONTLIE_KEY (e API_FOOTBALL_KEY p/ futebol) devem existir no
# Secret Manager e a service account de runtime precisa de roles/secretmanager.secretAccessor.
# A presença de API_FOOTBALL_KEY no .env continua decidindo se a chave de futebol é montada.
build_secrets() {
    SECRETS="BALLDONTLIE_KEY=BALLDONTLIE_KEY:latest"
    if [ -n "${API_FOOTBALL_KEY:-}" ]; then
        SECRETS="${SECRETS},API_FOOTBALL_KEY=API_FOOTBALL_KEY:latest"
    fi
    echo "$SECRETS"
}

# Faz deploy de um serviço
deploy_service() {
    local SERVICE_NAME=$1
    local SERVICE_DIR=$2
    
    print_info "Fazendo deploy de $SERVICE_NAME..."
    
    local SERVICE_PATH="$PROJECT_ROOT/cloud_run/$SERVICE_DIR"
    
    if [ ! -d "$SERVICE_PATH" ]; then
        print_error "Diretório do serviço não encontrado: $SERVICE_PATH"
        return 1
    fi
    
    if [ ! -f "$SERVICE_PATH/main.py" ]; then
        print_error "main.py não encontrado em $SERVICE_PATH"
        return 1
    fi
    
    if [ ! -f "$SERVICE_PATH/requirements.txt" ]; then
        print_error "requirements.txt não encontrado em $SERVICE_PATH"
        return 1
    fi
    
    # Prepara diretório temporário com src/ e scripts/
    local TEMP_DIR=$(mktemp -d)
    print_info "Preparando diretório temporário: $TEMP_DIR"
    
    # Copia arquivos do serviço (main.py, requirements.txt e opcionalmente Procfile)
    print_info "Copiando arquivos do serviço..."
    cp "$SERVICE_PATH/main.py" "$TEMP_DIR/"
    cp "$SERVICE_PATH/requirements.txt" "$TEMP_DIR/"
    if [ -f "$SERVICE_PATH/Procfile" ]; then
        cp "$SERVICE_PATH/Procfile" "$TEMP_DIR/"
    fi
    
    # Copia src/ e scripts/
    print_info "Copiando diretórios src/ e scripts/..."
    cp -r "$PROJECT_ROOT/src" "$TEMP_DIR/"
    cp -r "$PROJECT_ROOT/scripts" "$TEMP_DIR/"
    
    # Verifica se os arquivos foram copiados corretamente
    if [ ! -f "$TEMP_DIR/main.py" ]; then
        print_error "main.py não foi copiado corretamente"
        rm -rf "$TEMP_DIR"
        return 1
    fi
    
    if [ ! -d "$TEMP_DIR/src" ]; then
        print_error "Diretório src/ não foi copiado corretamente"
        rm -rf "$TEMP_DIR"
        return 1
    fi
    
    if [ ! -d "$TEMP_DIR/scripts" ]; then
        print_error "Diretório scripts/ não foi copiado corretamente"
        rm -rf "$TEMP_DIR"
        return 1
    fi
    
    print_info "Estrutura de arquivos preparada:"
    echo "  - main.py: $(test -f "$TEMP_DIR/main.py" && echo "✓" || echo "✗")"
    echo "  - requirements.txt: $(test -f "$TEMP_DIR/requirements.txt" && echo "✓" || echo "✗")"
    echo "  - src/: $(test -d "$TEMP_DIR/src" && echo "✓" || echo "✗")"
    echo "  - scripts/: $(test -d "$TEMP_DIR/scripts" && echo "✓" || echo "✗")"
    
    # Constrói env vars
    local ENTRY_POINT=$(get_entry_point "$SERVICE_NAME")
    local ENV_VARS=$(build_env_vars)
    ENV_VARS="${ENV_VARS},GOOGLE_FUNCTION_TARGET=${ENTRY_POINT}"

    # Faz deploy
    print_info "Executando gcloud run deploy..."
    print_info "Service account (runtime): $SERVICE_ACCOUNT"
    print_info "Entry point: $ENTRY_POINT"

    # DEPLOY_EXIT_CODE inicia em 0; cada gcloud usa `|| DEPLOY_EXIT_CODE=$?` para que
    # `set -e` não aborte o loop quando um serviço falha (tratamento abaixo preserva o resumo).
    local DEPLOY_EXIT_CODE=0

    # notify-execution usa secrets do Secret Manager em vez de env vars padrão
    if [ "$SERVICE_NAME" = "notify-execution" ]; then
        gcloud run deploy "$SERVICE_NAME" \
            --source "$TEMP_DIR" \
            --region "$REGION" \
            --platform managed \
            --no-allow-unauthenticated \
            --service-account "$SERVICE_ACCOUNT" \
            --memory "$MEMORY" \
            --cpu "$CPU" \
            --timeout "$TIMEOUT" \
            --set-env-vars "GOOGLE_FUNCTION_TARGET=${ENTRY_POINT}" \
            --set-secrets "GMAIL_USER=GMAIL_USER:latest,GMAIL_APP_PASSWORD=GMAIL_APP_PASSWORD:latest,NOTIFY_EMAIL=NOTIFY_EMAIL:latest" \
            --project "$GCP_PROJECT_ID" || DEPLOY_EXIT_CODE=$?
    elif [ "$SERVICE_NAME" = "sync-bq-to-postgres" ]; then
        # Sync precisa de 1Gi (carrega CSV em memória) + dois secrets (PRD e DEV).
        # Workflow agendado bate em ?env=prd e depois ?env=dev sequencialmente.
        # Timeout 900s (~15 min) é suficiente p/ volume atual (~250k linhas total).
        # max-instances=1: sync é serial, evitar concorrência destrutiva.
        # Python 3.13 pinado: psycopg[binary]==3.2.3 não tem wheels pra cp314 ainda.
        gcloud run deploy "$SERVICE_NAME" \
            --source "$TEMP_DIR" \
            --region "$REGION" \
            --platform managed \
            --no-allow-unauthenticated \
            --service-account "$SERVICE_ACCOUNT" \
            --memory "1Gi" \
            --cpu "$CPU" \
            --timeout "900" \
            --max-instances "1" \
            --set-env-vars "GCP_PROJECT_ID=${GCP_PROJECT_ID},LOG_LEVEL=${LOG_LEVEL}" \
            --set-build-env-vars "GOOGLE_RUNTIME_VERSION=3.13,GOOGLE_FUNCTION_TARGET=${ENTRY_POINT}" \
            --set-secrets "SUPABASE_PG_URL_PRD=SUPABASE_PG_URL_PRD:latest,SUPABASE_PG_URL_DEV=SUPABASE_PG_URL_DEV:latest" \
            --project "$GCP_PROJECT_ID" || DEPLOY_EXIT_CODE=$?
    elif [ "$SERVICE_NAME" = "daily-summary" ]; then
        # daily-summary lê Cloud Logging + Workflow Executions e envia 1 email/dia
        # (resumo consolidado de TODOS os workflows). Mesmos secrets do notify-execution
        # (Gmail). Precisa de GCP_PROJECT_ID/LOG_LEVEL em env (src.config + logger).
        # SA runtime = $SERVICE_ACCOUNT (ExtractScripts@), que precisa de
        # roles/logging.viewer + roles/workflows.viewer (ver IAM no plano). Timeout 600s
        # folgado p/ paginação do Logging.
        gcloud run deploy "$SERVICE_NAME" \
            --source "$TEMP_DIR" \
            --region "$REGION" \
            --platform managed \
            --no-allow-unauthenticated \
            --service-account "$SERVICE_ACCOUNT" \
            --memory "$MEMORY" \
            --cpu "$CPU" \
            --timeout "600" \
            --set-env-vars "GCP_PROJECT_ID=${GCP_PROJECT_ID},LOG_LEVEL=${LOG_LEVEL}" \
            --set-build-env-vars "GOOGLE_FUNCTION_TARGET=${ENTRY_POINT}" \
            --set-secrets "GMAIL_USER=GMAIL_USER:latest,GMAIL_APP_PASSWORD=GMAIL_APP_PASSWORD:latest,NOTIFY_EMAIL=NOTIFY_EMAIL:latest" \
            --project "$GCP_PROJECT_ID" || DEPLOY_EXIT_CODE=$?
    else
        gcloud run deploy "$SERVICE_NAME" \
            --source "$TEMP_DIR" \
            --region "$REGION" \
            --platform managed \
            --no-allow-unauthenticated \
            --service-account "$SERVICE_ACCOUNT" \
            --memory "$MEMORY" \
            --cpu "$CPU" \
            --timeout "$TIMEOUT" \
            --set-env-vars "$ENV_VARS" \
            --set-secrets "$(build_secrets)" \
            --set-build-env-vars "GOOGLE_FUNCTION_TARGET=${ENTRY_POINT}" \
            --project "$GCP_PROJECT_ID" || DEPLOY_EXIT_CODE=$?
    fi

    if [ "$DEPLOY_EXIT_CODE" -eq 0 ]; then
        print_info "✓ Deploy de $SERVICE_NAME concluído com sucesso"
        
        # Aguarda um pouco para garantir que o serviço está totalmente pronto
        print_info "Aguardando serviço ficar pronto..."
        sleep 5
        
        # Obtém URL do serviço
        local SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
            --region "$REGION" \
            --project "$GCP_PROJECT_ID" \
            --format="value(status.url)" 2>/dev/null)
        
        if [ -n "$SERVICE_URL" ]; then
            print_info "URL do serviço: $SERVICE_URL"
            
            # Verifica se o serviço está realmente pronto
            local READY_STATUS=$(gcloud run services describe "$SERVICE_NAME" \
                --region "$REGION" \
                --project "$GCP_PROJECT_ID" \
                --format="value(status.conditions[0].status)" 2>/dev/null)
            
            if [ "$READY_STATUS" = "True" ]; then
                print_info "✓ Serviço está pronto e ativo"
            else
                print_warning "Serviço pode ainda estar inicializando. Status: $READY_STATUS"
            fi
        else
            print_warning "Não foi possível obter a URL do serviço, mas o deploy pode ter sido bem-sucedido"
        fi
    else
        print_error "✗ Falha no deploy de $SERVICE_NAME"
        print_error "Código de saída: $DEPLOY_EXIT_CODE"
        echo ""
        print_info "Verificando se o serviço foi criado mesmo assim..."
        # Tenta verificar se o serviço existe
        if gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$GCP_PROJECT_ID" &>/dev/null; then
            print_warning "O serviço existe! O erro pode ter sido apenas na visualização de logs."
            local SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
                --region "$REGION" \
                --project "$GCP_PROJECT_ID" \
                --format="value(status.url)" 2>/dev/null)
            if [ -n "$SERVICE_URL" ]; then
                print_info "URL do serviço: $SERVICE_URL"
            fi
        else
            print_error "O serviço não foi criado. Verifique os logs no console do GCP:"
            echo "  https://console.cloud.google.com/cloud-build/builds?project=$GCP_PROJECT_ID"
            echo ""
            print_info "Nota: Se você não tem permissão para ver logs, peça a um administrador para verificar."
        fi
        echo ""
        rm -rf "$TEMP_DIR"
        return 1
    fi
    
    # Limpa diretório temporário
    rm -rf "$TEMP_DIR"
    
    return 0
}

# Lista serviços deployados
list_services() {
    print_info "Serviços deployados:"
    gcloud run services list \
        --region "$REGION" \
        --project "$GCP_PROJECT_ID" \
        --format="table(metadata.name,status.url,status.conditions[0].status)" \
        --filter="metadata.name:extract-*"
}

# Função principal
main() {
    print_info "=== Deploy de Serviços Cloud Run ==="
    echo ""
    
    # Verificações
    check_gcloud
    check_auth
    load_env
    
    echo ""
    print_info "Configurações:"
    echo "  Região: $REGION"
    echo "  Projeto: $GCP_PROJECT_ID"
    echo "  Service Account (runtime): $SERVICE_ACCOUNT"
    echo "  Memória: $MEMORY"
    echo "  CPU: $CPU"
    echo "  Timeout: ${TIMEOUT}s"
    echo ""
    
    # Verifica e avisa sobre service account do Cloud Build
    local PROJECT_NUMBER=$(gcloud projects describe "$GCP_PROJECT_ID" --format="value(projectNumber)" 2>/dev/null)
    if [ -n "$PROJECT_NUMBER" ]; then
        local CLOUD_BUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
        local COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
        print_warning "Nota: O Cloud Build usa a service account padrão durante o build:"
        echo "  - Cloud Build: $CLOUD_BUILD_SA"
        echo "  - Compute Engine: $COMPUTE_SA"
        echo ""
        print_info "Certifique-se de que essas service accounts também têm permissões de Storage."
        echo ""
    fi
    
    # Resolve o(s) serviço(s) a deployar a partir dos argumentos.
    #
    # Formas suportadas:
    #   ./deploy_cloud_run.sh                              -> tudo
    #   ./deploy_cloud_run.sh <sport>                      -> todos do esporte (nba|futebol)
    #   ./deploy_cloud_run.sh <service>                    -> back-compat (1 serviço)
    #   ./deploy_cloud_run.sh <sport> <service>            -> 1 serviço, valida que pertence ao esporte
    local SERVICES_TO_DEPLOY=()

    if [ $# -eq 0 ]; then
        SERVICES_TO_DEPLOY=("${SERVICES[@]}")
        print_info "Fazendo deploy de TODOS os serviços (${#SERVICES_TO_DEPLOY[@]} no total)..."

    elif [ $# -eq 1 ]; then
        if is_supported_sport "$1"; then
            local sport=$1
            local arr_name="$(echo "$sport" | tr '[:lower:]' '[:upper:]')_SERVICES[@]"
            SERVICES_TO_DEPLOY=("${!arr_name}")
            local sport_upper
            sport_upper=$(echo "$sport" | tr '[:lower:]' '[:upper:]')
            print_info "Fazendo deploy de TODOS os serviços de ${sport_upper} (${#SERVICES_TO_DEPLOY[@]})..."
        else
            local SERVICE_NAME=$1
            local SERVICE_DIR
            SERVICE_DIR=$(get_service_dir "$SERVICE_NAME")
            if [ -z "$SERVICE_DIR" ]; then
                print_error "'$SERVICE_NAME' não é um esporte nem um serviço conhecido"
                echo ""
                echo "Esportes:"
                for sport in "${SUPPORTED_SPORTS[@]}"; do echo "  - $sport"; done
                echo "Serviços:"
                for service in "${SERVICES[@]}"; do echo "  - ${service%%:*}"; done
                exit 1
            fi
            SERVICES_TO_DEPLOY=("$SERVICE_NAME:$SERVICE_DIR")
        fi

    elif [ $# -eq 2 ]; then
        local sport=$1
        local SERVICE_NAME=$2

        if ! is_supported_sport "$sport"; then
            print_error "Esporte '$sport' não suportado"
            echo "Esportes disponíveis: ${SUPPORTED_SPORTS[*]}"
            exit 1
        fi

        if ! service_belongs_to_sport "$SERVICE_NAME" "$sport"; then
            print_error "Serviço '$SERVICE_NAME' não pertence ao esporte '$sport'"
            echo ""
            echo "Serviços de $(echo "$sport" | tr '[:lower:]' '[:upper:]'):"
            local arr_name="$(echo "$sport" | tr '[:lower:]' '[:upper:]')_SERVICES[@]"
            for entry in "${!arr_name}"; do echo "  - ${entry%%:*}"; done
            exit 1
        fi

        local SERVICE_DIR
        SERVICE_DIR=$(get_service_dir "$SERVICE_NAME")
        SERVICES_TO_DEPLOY=("$SERVICE_NAME:$SERVICE_DIR")
        print_info "Fazendo deploy de $(echo "$sport" | tr '[:lower:]' '[:upper:]')/$SERVICE_NAME..."

    else
        print_error "Argumentos inválidos. Uso:"
        echo "  $0                      # tudo"
        echo "  $0 <sport>              # todos do esporte (nba|futebol)"
        echo "  $0 <service>            # 1 serviço (back-compat)"
        echo "  $0 <sport> <service>    # 1 serviço, validado pelo esporte"
        exit 1
    fi

    echo ""
    SUCCESS_COUNT=0
    FAIL_COUNT=0

    for service in "${SERVICES_TO_DEPLOY[@]}"; do
        local SERVICE_NAME="${service%%:*}"
        local SERVICE_DIR="${service#*:}"

        if deploy_service "$SERVICE_NAME" "$SERVICE_DIR"; then
            SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        else
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi

        echo ""
    done

    # Mostra resumo quando há mais de 1 serviço
    if [ "${#SERVICES_TO_DEPLOY[@]}" -gt 1 ]; then
        echo ""
        print_info "=== Resumo do Deploy ==="
        echo "  Sucessos: $SUCCESS_COUNT"
        echo "  Falhas: $FAIL_COUNT"
        echo ""

        if [ $FAIL_COUNT -eq 0 ]; then
            list_services
        fi
    fi
}

# Executa função principal
main "$@"

