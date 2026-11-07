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
        echo "  SYNC_SERVICE_ACCOUNT=sync-bq-postgres (opcional: conta de runtime SÓ do sync-bq-to-postgres)"
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

    # Conta de runtime DEDICADA ao sync (DE#109, história 51 da #112). O sync lê o BigQuery por
    # query job (retenção de DEV da DE#106 e cache de serving de PRD da DE#109) e isso exige
    # `bigquery.jobs.create` (roles/bigquery.jobUser). Dar esse papel à conta compartilhada pelos
    # 29 serviços daria criação de query jobs a todos; por isso o papel entra numa conta só do
    # sync (`sync-bq-postgres@`), e esta variável a aplica SÓ ao `sync-bq-to-postgres`. Sem ela o
    # sync fica na `SERVICE_ACCOUNT` (comportamento de sempre; sem `jobUser` ali, o pré-voo do
    # sync aborta antes de qualquer TRUNCATE, fail-closed). Aceita nome curto como a outra.
    if [ -n "${SYNC_SERVICE_ACCOUNT:-}" ]; then
        if [[ "$SYNC_SERVICE_ACCOUNT" != *"@"* ]]; then
            SYNC_SERVICE_ACCOUNT="${SYNC_SERVICE_ACCOUNT}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
        fi
        print_info "sync-bq-to-postgres usará a conta dedicada: $SYNC_SERVICE_ACCOUNT"
    else
        SYNC_SERVICE_ACCOUNT="$SERVICE_ACCOUNT"
        print_warning "SYNC_SERVICE_ACCOUNT não definida: o sync-bq-to-postgres fica na conta compartilhada ($SERVICE_ACCOUNT). Sem bigquery.jobUser nela o sync aborta no pré-voo; use SYNC_SERVICE_ACCOUNT=sync-bq-postgres (ver o runbook da DE#109)."
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

# Lê o JSON inteiro de `gcloud run services describe` de um serviço, com o stderr à parte
# (um aviso do gcloud no caminho de sucesso não pode contaminar o JSON). Imprime o JSON em
# stdout; em falha imprime o erro em stderr e devolve 1. Uma leitura só serve à tag `live`
# e à conferência do carimbo, que são as duas leituras pós-deploy.
ler_servico_json() {
    local service_name=$1
    local tmp_err out rc=0
    tmp_err=$(mktemp)
    out=$(gcloud run services describe "$service_name" \
        --region "$REGION" \
        --project "$GCP_PROJECT_ID" \
        --format=json 2>"$tmp_err") || rc=$?
    if [ "$rc" -ne 0 ]; then
        cat "$tmp_err" >&2
        rm -f "$tmp_err"
        return 1
    fi
    rm -f "$tmp_err"
    printf '%s' "$out"
}

# Tag `live` no Artifact Registry (ver infra/README.md e ADR 0007).
#
# A cleanup policy do repo `cloud-run-source-deploy` só pode apagar imagem velha se o
# digest que o serviço SERVE estiver protegido. Os 29 serviços escalam a zero, então o
# cold start puxa a imagem do registry de novo: digest vivo apagado = serviço que não
# sobe. A tag `latest` não serve de proteção: o `gcloud run deploy --source` move a
# `latest` no push, ANTES de a revisão ficar pronta, e 5 pushes medidos em 07/11/2026
# nunca viraram revisão. Por isso a fonte da verdade aqui é o tráfego do serviço
# (`status.traffic`, revisões com percent > 0) e o `status.imageDigest` de cada revisão,
# e não o que acabou de ser empurrado.
#
# Nomes de tag FIXOS: `live` na revisão com mais tráfego e `live-prev` na segunda (traffic
# split ou rollback parcial). A policy mantém tudo com prefixo `live`, então um nome por
# revisão (`live-<rev>`) viraria um Keep permanente que ninguém remove, e a cleanup
# deixaria de limpar. Mais de duas revisões com tráfego é estado que este script não sabe
# proteger com dois nomes: falha alto em vez de escolher.
#
# Fail-closed: erro de leitura do tráfego ou da revisão, tráfego vazio ou digest fora do
# formato `<host>-docker.pkg.dev/...@sha256:<64 hex>` devolvem 1 sem aplicar tag nenhuma.
#
# $2: o JSON de `services describe` já lido pelo chamador (uma leitura serve às duas
# conferências pós-deploy).
marcar_live() {
    local service_name=$1
    local svc_json=$2
    local revisoes parse_rc=0

    revisoes=$(printf '%s' "$svc_json" | python3 -c '
import json, sys

doc = json.load(sys.stdin)
status = doc.get("status") or {}
soma = {}
for entrada in status.get("traffic") or []:
    pct = entrada.get("percent") or 0
    if pct <= 0:
        continue
    rev = entrada.get("revisionName")
    if not rev and entrada.get("latestRevision"):
        rev = status.get("latestReadyRevisionName")
    if not rev:
        sys.exit("entrada de status.traffic com percent > 0 e sem revisionName")
    soma[rev] = soma.get(rev, 0) + pct
if not soma:
    sys.exit("status.traffic sem nenhuma revisao com percent > 0")
if len(soma) > 2:
    sys.exit("%d revisoes com trafego (%s): so ha dois nomes de tag (live, live-prev)"
             % (len(soma), ", ".join(sorted(soma))))
# Mais tráfego primeiro; empate (50/50) desempata pela revisão mais nova (o nome
# termina em -NNNNN-xxx, crescente). Dois sorts estáveis: nome desc, depois percent desc.
ordem = sorted(soma.items(), key=lambda kv: kv[0], reverse=True)
for rev, _ in sorted(ordem, key=lambda kv: -kv[1]):
    print(rev)
' 2>&1) || parse_rc=$?
    if [ "$parse_rc" -ne 0 ]; then
        print_error "✗ Erro de LEITURA do tráfego de $service_name; tag live NÃO aplicada:"
        echo "$revisoes" | sed 's/^/     /' >&2
        return 1
    fi

    local nomes_tag=("live" "live-prev")
    local i=0 rev rev_json digest pacote="" pacote_rev tmp_err rc
    local digests=()
    while IFS= read -r rev; do
        [ -z "$rev" ] && continue
        tmp_err=$(mktemp)
        rc=0
        rev_json=$(gcloud run revisions describe "$rev" \
            --region "$REGION" \
            --project "$GCP_PROJECT_ID" \
            --format=json 2>"$tmp_err") || rc=$?
        if [ "$rc" -ne 0 ]; then
            print_error "✗ Erro de LEITURA da revisão $rev de $service_name; tag live NÃO aplicada:"
            sed 's/^/     /' "$tmp_err" >&2
            rm -f "$tmp_err"
            return 1
        fi
        rm -f "$tmp_err"
        rc=0
        digest=$(printf '%s' "$rev_json" | python3 -c '
import json, re, sys

d = (json.load(sys.stdin).get("status") or {}).get("imageDigest") or ""
if not re.fullmatch(r"[a-z0-9-]+-docker\.pkg\.dev/[^@\s]+@sha256:[0-9a-f]{64}", d):
    sys.exit("status.imageDigest ausente ou fora do formato: %r" % d)
print(d)
' 2>&1) || rc=$?
        if [ "$rc" -ne 0 ]; then
            print_error "✗ Erro de LEITURA do digest da revisão $rev de $service_name; tag live NÃO aplicada:"
            echo "$digest" | sed 's/^/     /' >&2
            return 1
        fi
        pacote_rev="${digest%@*}"
        if [ -n "$pacote" ] && [ "$pacote_rev" != "$pacote" ]; then
            print_error "✗ Revisões de $service_name em pacotes diferentes ($pacote, $pacote_rev); tag live NÃO aplicada"
            return 1
        fi
        pacote="$pacote_rev"
        digests+=("$digest")
    done <<< "$revisoes"

    # Duas leituras primeiro, escrita depois: nenhuma tag sai se uma das revisões falhar.
    for digest in "${digests[@]}"; do
        if [ "$i" -eq 1 ] && [ "$digest" = "${digests[0]}" ]; then
            break  # as duas revisões servem o mesmo digest; `live` já o protege
        fi
        if [ "${LIVE_SO_LISTAR:-0}" -eq 1 ]; then
            # `--lista-vivos`: mesma leitura, nenhuma escrita. Digest completo, para o
            # scripts/simula_cleanup_ar.py conferir que a policy não apaga nada vivo.
            echo "vivo ${service_name} ${nomes_tag[$i]} ${digest}"
            i=$((i + 1))
            continue
        fi
        if ! gcloud artifacts docker tags add "$digest" "${pacote}:${nomes_tag[$i]}" --quiet >/dev/null; then
            print_error "✗ Falha ao aplicar a tag ${nomes_tag[$i]} em $digest"
            return 1
        fi
        print_info "✓ Tag ${nomes_tag[$i]} → ${digest##*/}"
        i=$((i + 1))
    done
    return 0
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
    if [ "$SERVICE_NAME" = "sync-bq-to-postgres" ]; then
        print_info "Service account (runtime): $SYNC_SERVICE_ACCOUNT"
    else
        print_info "Service account (runtime): $SERVICE_ACCOUNT"
    fi
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
        # SERIALIZAÇÃO (DE#107): o que impede dois syncs do mesmo (sport, env) NÃO é o
        # `--max-instances 1`. Ele limita a UMA instância, mas o `containerConcurrency` do
        # Cloud Run é 80 (default), então essa mesma instância aceita várias requisições
        # ao mesmo tempo (foi assim que o retry do workflow rodou o sync duas vezes, na
        # mesma instância, todo dia desde 01/09). O mecanismo real é a trava no Postgres:
        # `pg_try_advisory_lock` de sessão por (sport, env), em src/sync/trava.py; ocupada,
        # o handler responde 409 e o workflow trata como "em andamento". O max-instances=1
        # fica só como teto de memória/conexões (2Gi, uma instância).
        # --timeout 3600 (era 900): o rebuild das 13h leva ~1100-1470 s. O workflow usa
        # http.get de 1800 s (máximo do Workflows) e o statement_timeout da sessão do
        # sync é 3600 s (SYNC_STATEMENT_TIMEOUT_S): nunca pode passar do timeout daqui.
        # ORDEM DE DEPLOY: workflow-futebol-sync (YAML que trata 409) ANTES desta imagem.
        # CONTA DE RUNTIME (DE#109): $SYNC_SERVICE_ACCOUNT (dedicada, com bigquery.jobUser), que
        # cai em $SERVICE_ACCOUNT quando a variável não está definida. É a única ramificação que
        # a usa: as outras 28 seguem na conta compartilhada.
        # Python 3.13 pinado: psycopg[binary]==3.2.3 não tem wheels pra cp314 ainda.
        gcloud run deploy "$SERVICE_NAME" \
            --source "$TEMP_DIR" \
            --region "$REGION" \
            --platform managed \
            --no-allow-unauthenticated \
            --service-account "$SYNC_SERVICE_ACCOUNT" \
            --memory "2Gi" \
            --cpu "$CPU" \
            --timeout "3600" \
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

        # Uma leitura só do serviço, para as duas conferências pós-deploy abaixo (tag
        # `live` e carimbo). Erro de LEITURA (API/IAM/parse) é estado diferente de
        # "carimbo ausente" — mesma distinção do checa_deriva_servicos.sh. Sem isto, uma
        # falha transitória do `describe` logo após um deploy bom seria relatada como
        # "carimbo não voltou", que é a notícia errada.
        local CARIMBO_JSON
        if ! CARIMBO_JSON=$(ler_servico_json "$SERVICE_NAME" 2>&1); then
            print_error "✗ Erro de LEITURA ao reler $SERVICE_NAME (tag live e carimbo NÃO conferidos; não é deriva):"
            echo "$CARIMBO_JSON" | sed 's/^/     /' >&2
            print_error "  Depois de resolver: ./scripts/deploy_cloud_run.sh --so-tag-live $SERVICE_NAME"
            rm -rf "$TEMP_DIR"
            return 1
        fi

        # Tag `live` ANTES da conferência do carimbo, e independente dela: o deploy já
        # subiu e o tráfego já está no digest novo. Se a tag viesse depois do `return 1`
        # do carimbo, um carimbo que não confere deixaria a tag num digest MORTO e o vivo
        # desprotegido da cleanup policy do Artifact Registry.
        local TAG_FALHOU=0
        if ! marcar_live "$SERVICE_NAME" "$CARIMBO_JSON"; then
            TAG_FALHOU=1
            print_error "  A cleanup policy do Artifact Registry pode apagar o digest vivo de $SERVICE_NAME."
            print_error "  Depois de resolver: ./scripts/deploy_cloud_run.sh --so-tag-live $SERVICE_NAME"
        fi

        # Leitura de conferência do carimbo (DE #50): carimbo que o próprio deploy não
        # confirma é carimbo que some na primeira refatoração — foi exatamente o
        # "segundo comando manual" que deixou o fix do de-vig 2 dias fora de produção
        # (decisão 6 do ADR 0001). Só roda para serviço já coberto pelo manifesto.
        if [ -n "$PROCEDENCIA_ESPERADA" ]; then
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

        if [ "$TAG_FALHOU" -ne 0 ]; then
            rm -rf "$TEMP_DIR"
            return 1
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
            # Se o deploy subiu apesar do exit code, há um digest novo servindo tráfego
            # sem a tag `live`. Marca do mesmo jeito (o deploy continua reportado como
            # falha). Falha aqui só imprime: o retorno já é 1.
            local FALHA_JSON
            if FALHA_JSON=$(ler_servico_json "$SERVICE_NAME" 2>&1); then
                marcar_live "$SERVICE_NAME" "$FALHA_JSON" || \
                    print_error "  Tag live não aplicada; depois de resolver: ./scripts/deploy_cloud_run.sh --so-tag-live $SERVICE_NAME"
            else
                print_error "  Não consegui reler $SERVICE_NAME para a tag live: ./scripts/deploy_cloud_run.sh --so-tag-live $SERVICE_NAME"
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

# Modo `--derivados`: reduz SERVICES_TO_DEPLOY (variável local do main(), visível aqui
# por escopo dinâmico) aos serviços cujo hash do disco diverge do PROCEDENCIA_HASH gravado
# no serviço. Quem decide o estado é o detector de deriva (`checa_deriva_servicos.sh
# --estados`), não uma segunda cópia da comparação aqui dentro.
#
# Cada deploy `--source` sobe ~4 versões no Artifact Registry (~127 MB de runtime Python
# por build). Deployar os 29 porque um mudou é o que encheu o repo `cloud-run-source-deploy`
# (61,9 GiB em 07/11/2026). Este modo existe para o deploy de rotina não fazer isso.
#
#   em-dia                         pula
#   deriva-nucleo/-svc/-ambos      deploya
#   sem-carimbo                    deploya (nunca passou pelo deploy carimbado)
#   erro (leitura ou hash local)   ABORTA antes do primeiro deploy
#   qualquer outra coisa           ABORTA (estado que este script não conhece)
#
# Nenhum estado duvidoso PULA: pular é a única escolha que deixa produção defasada sem
# ninguém ver. Serviço que ainda não existe no Cloud Run cai em "erro" (o describe falha);
# o remédio é deployá-lo por nome.
filtrar_derivados() {
    local nomes=() entry
    for entry in "${SERVICES_TO_DEPLOY[@]}"; do
        nomes+=("${entry%%:*}")
    done

    print_info "Consultando o detector de deriva para ${#nomes[@]} serviço(s)..."
    local estados rc=0
    estados=$(GCP_PROJECT_ID="$GCP_PROJECT_ID" GCP_REGION="$REGION" \
        "$SCRIPT_DIR/checa_deriva_servicos.sh" --estados "${nomes[@]}") || rc=$?
    if [ "$rc" -ne 0 ]; then
        print_error "checa_deriva_servicos.sh --estados falhou (exit $rc); nada foi deployado."
        return 1
    fi

    local derivados=() problemas=() nome estado n
    for entry in "${SERVICES_TO_DEPLOY[@]}"; do
        nome="${entry%%:*}"
        n=$(printf '%s\n' "$estados" | awk -F'\t' -v s="$nome" '$1 == s' | wc -l | tr -d ' ')
        if [ "$n" -ne 1 ]; then
            problemas+=("$nome: o detector devolveu $n linha(s) de estado")
            continue
        fi
        estado=$(printf '%s\n' "$estados" | awk -F'\t' -v s="$nome" '$1 == s { print $2 }')
        case "$estado" in
            em-dia)
                echo "  em dia (pulado): $nome"
                ;;
            deriva-nucleo|deriva-svc|deriva-ambos|sem-carimbo)
                echo "  ${estado} → deploy: $nome"
                derivados+=("$entry")
                ;;
            erro)
                problemas+=("$nome: erro de leitura (detalhe acima)")
                ;;
            *)
                problemas+=("$nome: estado desconhecido '$estado'")
                ;;
        esac
    done

    if [ "${#problemas[@]}" -gt 0 ]; then
        print_error "--derivados abortado ANTES de qualquer deploy; estado incerto em:"
        local p
        for p in "${problemas[@]}"; do
            print_error "  $p"
        done
        print_error "Serviço novo (ainda não existe no Cloud Run)? Deploye-o por nome: $0 <servico>"
        return 1
    fi

    # `"${arr[@]}"` de array vazio é "unbound variable" no bash 3.2 com `set -u`.
    SERVICES_TO_DEPLOY=(${derivados[@]+"${derivados[@]}"})
    return 0
}

# Modo `--so-tag-live`: (re)aplica a tag `live` sem deployar. Backfill antes da cleanup
# policy do Artifact Registry e remédio de um deploy cuja tag falhou.
aplicar_so_tag_live() {
    local ok=0 falhas=0 service nome svc_json
    for service in "${SERVICES_TO_DEPLOY[@]}"; do
        nome="${service%%:*}"
        print_info "Tag live: $nome"
        if svc_json=$(ler_servico_json "$nome") && marcar_live "$nome" "$svc_json"; then
            ok=$((ok + 1))
        else
            print_error "✗ $nome: tag live NÃO aplicada"
            falhas=$((falhas + 1))
        fi
    done
    echo ""
    if [ "${LIVE_SO_LISTAR:-0}" -eq 1 ]; then
        print_info "=== Digests vivos: $ok ok, $falhas falha(s) de leitura ==="
    else
        print_info "=== Tag live: $ok ok, $falhas falha(s) ==="
    fi
    if [ "$falhas" -gt 0 ]; then
        exit 1
    fi
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
    echo "  Service Account do sync-bq-to-postgres: $SYNC_SERVICE_ACCOUNT"
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
    #   ./deploy_cloud_run.sh --derivados [sport]          -> só os que derivaram do disco
    #   ./deploy_cloud_run.sh --so-tag-live [alvo]         -> não deploya; só (re)aplica a tag live
    #   ./deploy_cloud_run.sh --lista-vivos [alvo]         -> só lista os digests que serviriam de live
    #
    # `--so-tag-live` aceita os mesmos alvos do deploy (nada, sport, service, sport service).
    # É o backfill da tag `live` antes de aplicar a cleanup policy do Artifact Registry, e o
    # remédio quando um deploy subiu mas a tag falhou.
    local SERVICES_TO_DEPLOY=()
    local MODO="deploy"

    if [ "${1:-}" = "--so-tag-live" ]; then
        MODO="tag"
        shift
    elif [ "${1:-}" = "--lista-vivos" ]; then
        MODO="tag"
        LIVE_SO_LISTAR=1
        shift
    elif [ "${1:-}" = "--derivados" ]; then
        MODO="derivados"
        shift
        # Só esporte (ou nada). `--derivados extract-games` não tem sentido: pedir UM
        # serviço é pedir o deploy dele, derivado ou não.
        if [ $# -gt 1 ] || { [ $# -eq 1 ] && ! is_supported_sport "$1"; }; then
            print_error "Uso: $0 --derivados [sport]   (sport: ${SUPPORTED_SPORTS[*]})"
            exit 1
        fi
    fi

    if [ $# -eq 0 ]; then
        SERVICES_TO_DEPLOY=("${SERVICES[@]}")
        print_info "Alvo: TODOS os serviços (${#SERVICES_TO_DEPLOY[@]} no total) — modo ${MODO}"

    elif [ $# -eq 1 ]; then
        if is_supported_sport "$1"; then
            local sport=$1
            local arr_name="$(echo "$sport" | tr '[:lower:]' '[:upper:]')_SERVICES[@]"
            SERVICES_TO_DEPLOY=("${!arr_name}")
            local sport_upper
            sport_upper=$(echo "$sport" | tr '[:lower:]' '[:upper:]')
            print_info "Alvo: TODOS os serviços de ${sport_upper} (${#SERVICES_TO_DEPLOY[@]}) — modo ${MODO}"
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
        print_info "Alvo: $(echo "$sport" | tr '[:lower:]' '[:upper:]')/$SERVICE_NAME — modo ${MODO}"

    else
        print_error "Argumentos inválidos. Uso:"
        echo "  $0                      # tudo"
        echo "  $0 <sport>              # todos do esporte (nba|futebol)"
        echo "  $0 <service>            # 1 serviço (back-compat)"
        echo "  $0 <sport> <service>    # 1 serviço, validado pelo esporte"
        echo "  $0 --derivados [sport]  # só os serviços cujo disco diverge do carimbo"
        echo "  $0 --so-tag-live [alvo] # só a tag live, sem deploy"
        echo "  $0 --lista-vivos [alvo] # lista os digests vivos (entrada do simula_cleanup_ar.py)"
        exit 1
    fi

    if [ "$MODO" = "tag" ]; then
        aplicar_so_tag_live
        return
    fi

    if [ "$MODO" = "derivados" ]; then
        if ! filtrar_derivados; then
            exit 1
        fi
        if [ "${#SERVICES_TO_DEPLOY[@]}" -eq 0 ]; then
            print_info "Nenhum serviço derivado: nada a deployar."
            return
        fi
        print_info "Deploy de ${#SERVICES_TO_DEPLOY[@]} serviço(s) derivado(s)..."
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

