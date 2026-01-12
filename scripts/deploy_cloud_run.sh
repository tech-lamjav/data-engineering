#!/bin/bash
# Script para fazer deploy de serviços Cloud Run
# Lê variáveis de ambiente do arquivo .env e faz deploy de todos os serviços ou um específico

set -e

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

# Lista de serviços (compatível com bash 3.x)
# Formato: SERVICE_NAME:SERVICE_DIR
SERVICES=(
    "extract-active-players:extract_active_players"
    "extract-games:extract_games"
    "extract-game-player-stats:extract_game_player_stats"
    "extract-season-averages:extract_season_averages"
    "extract-player-injuries:extract_player_injuries"
    "extract-team-standings:extract_team_standings"
    "extract-player-props:extract_player_props"
)

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
    if [ -z "$BALLDONTLIE_KEY" ]; then
        print_error "BALLDONTLIE_KEY não encontrada no .env"
        exit 1
    fi
    
    if [ -z "$GCS_BUCKET_NAME" ]; then
        print_error "GCS_BUCKET_NAME não encontrada no .env"
        exit 1
    fi
    
    if [ -z "$GCP_PROJECT_ID" ]; then
        print_error "GCP_PROJECT_ID não encontrada no .env"
        exit 1
    fi
    
    if [ -z "$SEASON" ]; then
        print_warning "SEASON não encontrada no .env, usando padrão: 2025"
        SEASON="2025"
    fi
    
    if [ -z "$LOG_LEVEL" ]; then
        LOG_LEVEL="INFO"
    fi
    
    # Service account (opcional, padrão: ExtractScripts)
    if [ -z "$SERVICE_ACCOUNT" ]; then
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
    ENV_VARS="BALLDONTLIE_KEY=${BALLDONTLIE_KEY}"
    ENV_VARS="${ENV_VARS},GCS_BUCKET_NAME=${GCS_BUCKET_NAME}"
    ENV_VARS="${ENV_VARS},GCP_PROJECT_ID=${GCP_PROJECT_ID}"
    ENV_VARS="${ENV_VARS},SEASON=${SEASON}"
    ENV_VARS="${ENV_VARS},LOG_LEVEL=${LOG_LEVEL}"
    echo "$ENV_VARS"
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
    
    # Copia arquivos do serviço (main.py e requirements.txt)
    print_info "Copiando arquivos do serviço..."
    cp "$SERVICE_PATH/main.py" "$TEMP_DIR/"
    cp "$SERVICE_PATH/requirements.txt" "$TEMP_DIR/"
    
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
    local ENV_VARS=$(build_env_vars)
    
    # Obtém entry point (nome da função)
    local ENTRY_POINT=$(get_entry_point "$SERVICE_NAME")
    
    # Faz deploy
    print_info "Executando gcloud run deploy..."
    print_info "Service account (runtime): $SERVICE_ACCOUNT"
    print_info "Entry point: $ENTRY_POINT"
    
    # Nota: O Cloud Build usa a service account padrão do Compute Engine durante o build
    # (PROJECT_NUMBER-compute@developer.gserviceaccount.com)
    # Essa service account também precisa ter permissões de Storage
    
    gcloud run deploy "$SERVICE_NAME" \
        --source "$TEMP_DIR" \
        --region "$REGION" \
        --platform managed \
        --no-allow-unauthenticated \
        --service-account "$SERVICE_ACCOUNT" \
        --memory "$MEMORY" \
        --cpu "$CPU" \
        --timeout "$TIMEOUT" \
        --entry-point "$ENTRY_POINT" \
        --set-env-vars "$ENV_VARS" \
        --project "$GCP_PROJECT_ID" \
        --quiet
    
    local DEPLOY_EXIT_CODE=$?
    
    if [ $DEPLOY_EXIT_CODE -eq 0 ]; then
        print_info "✓ Deploy de $SERVICE_NAME concluído com sucesso"
        
        # Obtém URL do serviço
        local SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
            --region "$REGION" \
            --project "$GCP_PROJECT_ID" \
            --format="value(status.url)" 2>/dev/null)
        
        if [ -n "$SERVICE_URL" ]; then
            print_info "URL do serviço: $SERVICE_URL"
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
    
    # Verifica se foi passado um serviço específico
    if [ $# -eq 1 ]; then
        SERVICE_NAME=$1
        SERVICE_DIR=$(get_service_dir "$SERVICE_NAME")
        
        if [ -z "$SERVICE_DIR" ]; then
            print_error "Serviço '$SERVICE_NAME' não encontrado"
            echo ""
            echo "Serviços disponíveis:"
            for service in "${SERVICES[@]}"; do
                echo "  - ${service%%:*}"
            done
            exit 1
        fi
        
        deploy_service "$SERVICE_NAME" "$SERVICE_DIR"
    else
        # Deploy de todos os serviços
        print_info "Fazendo deploy de todos os serviços..."
        echo ""
        
        SUCCESS_COUNT=0
        FAIL_COUNT=0
        
        for service in "${SERVICES[@]}"; do
            SERVICE_NAME="${service%%:*}"
            SERVICE_DIR="${service#*:}"
            
            if deploy_service "$SERVICE_NAME" "$SERVICE_DIR"; then
                SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
            else
                FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
            
            echo ""
        done
        
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

