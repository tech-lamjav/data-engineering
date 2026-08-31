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

# Carimbo de procedência (DE #44 / #50) para UM serviço, ou string vazia se o serviço
# ainda não está no manifesto do `procedencia_servicos.sh` — hoje só `extract-games`
# (tracer bullet; os outros 28 entram na DE #51, junto da extensão desta função às
# quatro ramificações de verdade).
#
# Falha (return 1) só quando o serviço ESTÁ no manifesto e ALGUM dos três hashes não
# pôde ser calculado (path declarada sumiu do disco, ou falha transitória do git) —
# isso é manifesto quebrado, não escopo, e não pode deployar em silêncio com um
# componente vazio. Serviço fora do manifesto (exit 3 do procedencia_servicos.sh) não
# é erro: devolve vazio e o deploy segue como sempre foi.
#
# $2 (opcional): path de um arquivo onde o hash COMBINADO é gravado, para o chamador
# reler sem recalcular — `comuns=$(carimbo_env_vars ...)` roda em SUBSHELL, então uma
# variável setada aqui dentro não escapa; um arquivo escapa.
carimbo_env_vars() {
    local service_name=$1
    local hash_file="${2:-}"
    local hash_combinado hash_nucleo hash_svc sha
    local tmp_err rc=0

    tmp_err=$(mktemp)
    hash_combinado=$("$SCRIPT_DIR/procedencia_servicos.sh" "$service_name" 2>"$tmp_err") || rc=$?

    if [ "$rc" -eq 3 ]; then
        rm -f "$tmp_err"
        echo ""
        return 0
    elif [ "$rc" -ne 0 ]; then
        print_error "carimbo de procedencia falhou para $service_name:"
        cat "$tmp_err" >&2
        rm -f "$tmp_err"
        return 1
    fi
    rm -f "$tmp_err"

    rc=0
    hash_nucleo=$("$SCRIPT_DIR/procedencia_servicos.sh" "$service_name" --nucleo) || rc=$?
    if [ "$rc" -ne 0 ]; then
        print_error "carimbo de procedencia (--nucleo) falhou para $service_name"
        return 1
    fi

    rc=0
    hash_svc=$("$SCRIPT_DIR/procedencia_servicos.sh" "$service_name" --svc) || rc=$?
    if [ "$rc" -ne 0 ]; then
        print_error "carimbo de procedencia (--svc) falhou para $service_name"
        return 1
    fi

    sha=$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo "sem-git")

    [ -n "$hash_file" ] && printf '%s' "$hash_combinado" > "$hash_file"
    echo "PROCEDENCIA_HASH=${hash_combinado},PROCEDENCIA_HASH_NUCLEO=${hash_nucleo},PROCEDENCIA_HASH_SVC=${hash_svc},PROCEDENCIA_SHA=${sha}"
}

# Ponto ÚNICO de montagem do --set-env-vars de runtime dos 29 serviços.
#
# Apenas config NÃO sensível. As chaves de API vão via Secret Manager (build_secrets).
#
# As ramificações do deploy_service() divergem de propósito, e a divergência está
# preservada — mas num lugar só:
#
#   notify-execution               só o entry point; não lê GCS, BigQuery nem SEASON
#   sync-bq-to-postgres            projeto + log (src.config e o logger precisam), sem
#   daily-summary                  bucket nem SEASON: nenhum dos dois extrai nada
#   os outros 26 (extractors)      a configuração de extração completa
#
# ⚠️ Variável que vale para TODOS os serviços entra no bloco COMUM do fim — uma
# edição, não quatro. Foi "esquecer uma ramificação" que deixou o fix do de-vig 2
# dias fora de produção; é a mesma classe de falha que o carimbo de procedência dos
# serviços existe para pegar (DE #44; o ADR chega em docs/adr/ pelo PR #57).
#
# Devolve 1 (sem imprimir nada em stdout) se o carimbo de um serviço coberto falhar —
# ver carimbo_env_vars(). O chamador (deploy_service) precisa checar o código de saída;
# `local vars=$(build_service_env_vars ...)` mascararia isso (o exit status vira o do
# `local`, não o do comando substituído).
build_service_env_vars() {
    local service_name=$1
    local entry_point=$2
    local hash_file="${3:-}"
    local vars

    case "$service_name" in
        notify-execution)
            vars="GOOGLE_FUNCTION_TARGET=${entry_point}"
            ;;
        sync-bq-to-postgres|daily-summary)
            vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},LOG_LEVEL=${LOG_LEVEL}"
            ;;
        *)
            vars="GCS_BUCKET_NAME=${GCS_BUCKET_NAME}"
            vars="${vars},GCP_PROJECT_ID=${GCP_PROJECT_ID}"
            vars="${vars},SEASON=${SEASON}"
            vars="${vars},LOG_LEVEL=${LOG_LEVEL}"
            vars="${vars},GOOGLE_FUNCTION_TARGET=${entry_point}"
            ;;
    esac

    # Bloco COMUM: o que vale para os 29, independente da ramificação — hoje só o
    # carimbo de procedência, e só para quem já está no manifesto (ver acima).
    local comuns
    comuns=$(carimbo_env_vars "$service_name" "$hash_file") || return 1
    if [ -n "$comuns" ]; then
        vars="${vars},${comuns}"
    fi

    echo "$vars"
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
    
    # Constrói env vars — todas as ramificações abaixo consomem este mesmo $ENV_VARS.
    # Declaração e atribuição SEPARADAS de propósito: `local x=$(cmd)` mascara o exit
    # code de `cmd` pelo do próprio `local` — `set -e` não pegaria uma falha aqui.
    #
    # PROCEDENCIA_HASH_FILE: onde build_service_env_vars()/carimbo_env_vars() gravam o
    # hash combinado, para a leitura de conferência pós-deploy reler em vez de chamar o
    # procedencia_servicos.sh uma terceira vez (ele já roda 3x por dentro do bloco
    # acima — combinado, --nucleo, --svc). Fica vazio (mktemp intocado) se o serviço
    # não estiver no manifesto.
    local ENTRY_POINT
    ENTRY_POINT=$(get_entry_point "$SERVICE_NAME")
    local PROCEDENCIA_HASH_FILE
    PROCEDENCIA_HASH_FILE=$(mktemp)
    local ENV_VARS
    ENV_VARS=$(build_service_env_vars "$SERVICE_NAME" "$ENTRY_POINT" "$PROCEDENCIA_HASH_FILE") || {
        print_error "Não foi possível montar as env vars de $SERVICE_NAME (carimbo de procedência)"
        rm -f "$PROCEDENCIA_HASH_FILE"
        rm -rf "$TEMP_DIR"
        return 1
    }
    local PROCEDENCIA_ESPERADA
    PROCEDENCIA_ESPERADA=$(cat "$PROCEDENCIA_HASH_FILE")
    rm -f "$PROCEDENCIA_HASH_FILE"

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
            --set-env-vars "$ENV_VARS" \
            --set-secrets "GMAIL_USER=GMAIL_USER:latest,GMAIL_APP_PASSWORD=GMAIL_APP_PASSWORD:latest,NOTIFY_EMAIL=NOTIFY_EMAIL:latest" \
            --project "$GCP_PROJECT_ID" || DEPLOY_EXIT_CODE=$?
    elif [ "$SERVICE_NAME" = "sync-bq-to-postgres" ]; then
        # Sync precisa de 2Gi: o COPY é streaming, mas o RSS do processo acumula
        # entre os requests PRD e DEV na mesma instância (max-instances=1) e com
        # 1Gi houve OOM kill em 10-12/07/2026 (pico 1,1Gi; ~700k linhas no total).
        # Dois secrets (PRD e DEV); workflow bate em ?env=prd e depois ?env=dev.
        # max-instances=1: sync é serial, evitar concorrência destrutiva.
        # Python 3.13 pinado: psycopg[binary]==3.2.3 não tem wheels pra cp314 ainda.
        gcloud run deploy "$SERVICE_NAME" \
            --source "$TEMP_DIR" \
            --region "$REGION" \
            --platform managed \
            --no-allow-unauthenticated \
            --service-account "$SERVICE_ACCOUNT" \
            --memory "2Gi" \
            --cpu "$CPU" \
            --timeout "900" \
            --max-instances "1" \
            --set-env-vars "$ENV_VARS" \
            --set-build-env-vars "GOOGLE_RUNTIME_VERSION=3.13,GOOGLE_FUNCTION_TARGET=${ENTRY_POINT}" \
            --set-secrets "SUPABASE_PG_URL_PRD=SUPABASE_PG_URL_PRD:latest,SUPABASE_PG_URL_DEV=SUPABASE_PG_URL_DEV:latest" \
            --project "$GCP_PROJECT_ID" || DEPLOY_EXIT_CODE=$?
    elif [ "$SERVICE_NAME" = "daily-summary" ]; then
        # daily-summary lê Cloud Logging + Workflow Executions e envia 1 email/dia
        # (resumo consolidado de TODOS os workflows). Mesmos secrets do notify-execution
        # (Gmail) + API_FOOTBALL_KEY, que a seção de cota usa p/ bater o /status 1x/dia.
        # Sem ela o email ainda sai, com a seção degradada dizendo o motivo — mas a cota
        # some justamente do único canal de alarme do pipeline.
        # Precisa de GCP_PROJECT_ID/LOG_LEVEL em env (src.config + logger).
        # SA runtime = $SERVICE_ACCOUNT (ExtractScripts@), que precisa de
        # roles/logging.viewer + roles/workflows.viewer (ver IAM no plano). Timeout 600s
        # folgado p/ paginação do Logging.
        SUMMARY_SECRETS="GMAIL_USER=GMAIL_USER:latest,GMAIL_APP_PASSWORD=GMAIL_APP_PASSWORD:latest,NOTIFY_EMAIL=NOTIFY_EMAIL:latest"
        # Mesma regra do build_secrets: a presença no .env decide se a chave é montada.
        if [ -n "${API_FOOTBALL_KEY:-}" ]; then
            SUMMARY_SECRETS="${SUMMARY_SECRETS},API_FOOTBALL_KEY=API_FOOTBALL_KEY:latest"
        else
            print_warning "daily-summary sem API_FOOTBALL_KEY: a secao de cota vai sair degradada"
        fi
        gcloud run deploy "$SERVICE_NAME" \
            --source "$TEMP_DIR" \
            --region "$REGION" \
            --platform managed \
            --no-allow-unauthenticated \
            --service-account "$SERVICE_ACCOUNT" \
            --memory "$MEMORY" \
            --cpu "$CPU" \
            --timeout "600" \
            --set-env-vars "$ENV_VARS" \
            --set-build-env-vars "GOOGLE_FUNCTION_TARGET=${ENTRY_POINT}" \
            --set-secrets "$SUMMARY_SECRETS" \
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

        # Leitura de conferência do carimbo (DE #50): carimbo que o próprio deploy não
        # confirma é carimbo que some na primeira refatoração — foi exatamente o
        # "segundo comando manual" que deixou o fix do de-vig 2 dias fora de produção
        # (decisão 6 do ADR 0001). Só roda para serviço já coberto pelo manifesto.
        if [ -n "$PROCEDENCIA_ESPERADA" ]; then
            # Erro de LEITURA (API/IAM/parse) é estado diferente de "carimbo ausente" —
            # mesma distinção do checa_deriva_servicos.sh. Sem isto, uma falha
            # transitória do `describe` logo após um deploy bom seria relatada como
            # "carimbo não voltou", que é a notícia errada.
            local CARIMBO_JSON DESC_RC=0
            CARIMBO_JSON=$(gcloud run services describe "$SERVICE_NAME" \
                --region "$REGION" \
                --project "$GCP_PROJECT_ID" \
                --format=json 2>&1) || DESC_RC=$?

            if [ "$DESC_RC" -ne 0 ]; then
                print_error "✗ Erro de LEITURA ao reler $SERVICE_NAME para conferir o carimbo (não é deriva):"
                echo "$CARIMBO_JSON" | sed 's/^/     /' >&2
                rm -rf "$TEMP_DIR"
                return 1
            fi

            local CARIMBO_LIDO PARSE_RC=0
            CARIMBO_LIDO=$(echo "$CARIMBO_JSON" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
spec = doc["spec"]["template"]["spec"]
for var in spec["containers"][0].get("env") or []:
    if var.get("name") == "PROCEDENCIA_HASH":
        print(var.get("value") or "")
        break
') || PARSE_RC=$?

            if [ "$PARSE_RC" -ne 0 ]; then
                print_error "✗ Erro de LEITURA: não consegui interpretar a resposta de $SERVICE_NAME (não é deriva)"
                rm -rf "$TEMP_DIR"
                return 1
            fi

            if [ "$CARIMBO_LIDO" != "$PROCEDENCIA_ESPERADA" ]; then
                print_error "✗ O carimbo de procedência não voltou na leitura de conferência de $SERVICE_NAME"
                print_error "  esperado: $PROCEDENCIA_ESPERADA"
                print_error "  lido:     ${CARIMBO_LIDO:-<ausente>}"
                rm -rf "$TEMP_DIR"
                return 1
            fi
            print_info "✓ Carimbo de procedência conferido: $PROCEDENCIA_ESPERADA"
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

# Fail-closed nos dois sentidos entre a tabela de deploy (SERVICES, acima) e o
# manifesto de procedência (`procedencia_servicos.sh --list-servicos`) — DE #51.
#
# Um manifesto que "esquece" um serviço da tabela ficaria mudo sobre a deriva dele
# (a mesma classe de bug que este mecanismo inteiro existe para consertar, uma camada
# abaixo); um manifesto com uma entrada órfã (serviço removido da tabela, esquecido no
# manifesto) é o espelho do mesmo erro. Os dois abortam ANTES de qualquer `gcloud run
# deploy`, e para QUALQUER invocação — mesmo `./deploy_cloud_run.sh extract-games` —
# porque a checagem é sobre a tabela inteira, não sobre o alvo pedido: um drift em
# outro serviço não pode passar batido só porque ninguém pediu para deployá-lo agora.
#
# `--paths` de `procedencia_servicos.sh` só consulta o `case` em memória (sem tocar
# disco/git) — checagem rápida e sem efeito colateral.
checar_manifesto() {
    local faltando_no_manifesto=()
    local orfaos_no_manifesto=()
    local entry svc

    for entry in "${SERVICES[@]}"; do
        svc="${entry%%:*}"
        if ! "$SCRIPT_DIR/procedencia_servicos.sh" "$svc" --paths >/dev/null 2>&1; then
            faltando_no_manifesto+=("$svc")
        fi
    done

    local manifesto_svc
    while IFS= read -r manifesto_svc; do
        [ -z "$manifesto_svc" ] && continue
        if ! get_service_dir "$manifesto_svc" >/dev/null; then
            orfaos_no_manifesto+=("$manifesto_svc")
        fi
    done < <("$SCRIPT_DIR/procedencia_servicos.sh" --list-servicos)

    if [ "${#faltando_no_manifesto[@]}" -gt 0 ] || [ "${#orfaos_no_manifesto[@]}" -gt 0 ]; then
        print_error "Manifesto de procedencia fora de sincronia com a tabela de deploy:"
        if [ "${#faltando_no_manifesto[@]}" -gt 0 ]; then
            print_error "  na tabela do deploy mas AUSENTE do manifesto (procedencia_servicos.sh): ${faltando_no_manifesto[*]}"
        fi
        if [ "${#orfaos_no_manifesto[@]}" -gt 0 ]; then
            print_error "  no manifesto mas AUSENTE da tabela do deploy: ${orfaos_no_manifesto[*]}"
        fi
        print_error "Os dois arquivos evoluem juntos (DE #51) — corrija antes de fazer deploy."
        return 1
    fi
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

    if ! checar_manifesto; then
        exit 1
    fi

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

    # `if deploy_service ...; then ...; else ...; fi` sempre "sucede" como construção —
    # o corpo executado é uma atribuição aritmética, que sempre retorna 0 — então sem
    # isto o script inteiro saía com exit 0 mesmo com FAIL_COUNT>0, e isso incluía a
    # leitura de conferência do carimbo (DE #50) falhando num deploy de 1 serviço só,
    # o caso comum (`./deploy_cloud_run.sh extract-games`), onde nem o resumo acima
    # chega a imprimir. "O deploy falha" no aceite da issue tem de valer no exit code.
    if [ "$FAIL_COUNT" -gt 0 ]; then
        exit 1
    fi
}

# Executa função principal
main "$@"

