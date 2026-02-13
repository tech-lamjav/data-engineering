# NBA Data Extraction Pipeline

Pipeline modular para extração de dados da API [balldontlie.io](https://docs.balldontlie.io/) (NBA), otimizado para deploy como Cloud Functions no Google Cloud Platform (GCP).

## Visão Geral

Este projeto extrai dados da NBA para a temporada 2025 e armazena os JSONs no Google Cloud Storage seguindo uma estrutura organizada. Cada endpoint da API possui sua própria Cloud Function dedicada, facilitando o deploy e manutenção.

## Estrutura do Projeto

```
data-engineering/
├── src/                          # Código fonte principal
│   ├── config.py                # Configurações centralizadas
│   ├── clients/                 # Clientes de API
│   │   ├── base_client.py       # Cliente base com lógica comum
│   │   └── balldontlie_client.py # Cliente específico da API
│   ├── extractors/              # Extractors de dados
│   │   ├── base_extractor.py    # Classe base
│   │   ├── games_extractor.py
│   │   ├── game_player_stats_extractor.py
│   │   ├── season_averages_extractor.py
│   │   ├── team_season_averages_extractor.py
│   │   ├── active_players_extractor.py
│   │   ├── player_injuries_extractor.py
│   │   ├── team_standings_extractor.py
│   │   └── player_props_extractor.py
│   ├── storage/                 # Armazenamento
│   │   └── gcs_storage.py       # Gerenciamento de uploads no GCS
│   ├── bigquery/                # BigQuery
│   │   └── bigquery_client.py   # Cliente para gerenciar external tables
│   └── utils/                   # Utilitários
│       ├── logger.py            # Configuração de logging
│       └── helpers.py           # Funções auxiliares
├── cloud_run/                   # Cloud Run Services
│   ├── extract_games/
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── extract_game_player_stats/
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── extract_season_averages/
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── extract_team_season_averages/
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── extract_active_players/
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── extract_player_injuries/
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── extract_team_standings/
│   │   ├── main.py
│   │   └── requirements.txt
│   └── extract_player_props/
│       ├── main.py
│       └── requirements.txt
├── cloud_functions/             # Cloud Functions (Legado)
│   ├── extract_games/
│   ├── extract_game_player_stats/
│   ├── extract_season_averages/
│   ├── extract_active_players/
│   ├── extract_player_injuries/
│   ├── extract_team_standings/
│   └── extract_player_props/
├── scripts/                     # Scripts de execução
│   ├── extract_active_players.py
│   ├── extract_games.py
│   ├── extract_game_player_stats.py
│   ├── extract_season_averages.py
│   ├── extract_team_season_averages.py
│   ├── extract_player_injuries.py
│   ├── extract_team_standings.py
│   ├── extract_player_props.py
│   ├── create_bigquery_external_tables.py  # Criar external tables no BigQuery
│   ├── deploy_cloud_run.sh                 # Script de deploy
│   └── sql/                                # Scripts SQL
│       └── create_external_tables.sql      # SQL para criar external tables
└── tests/                       # Testes
```

## Explicação da Estrutura do Projeto

### `src/` - Código Fonte Principal

Contém toda a lógica de negócio do projeto, organizada em módulos especializados:

#### `src/config.py`
Centraliza todas as configurações do projeto:
- Variáveis de ambiente (API keys, bucket name, etc.)
- Configurações da API (URL base, timeout)
- Configurações do GCS
- Configurações específicas por endpoint
- Função helper para gerar caminhos no GCS

#### `src/clients/` - Clientes de API
- **`base_client.py`**: Cliente HTTP base com lógica comum:
  - Gerenciamento de sessões HTTP
  - Tratamento de erros
  - Paginação automática
  - Timeout configurável
- **`balldontlie_client.py`**: Cliente específico da API balldontlie.io:
  - Métodos para cada endpoint da API
  - Tratamento de parâmetros específicos
  - Consolidação de dados paginados

#### `src/extractors/` - Extractors de Dados
Cada extractor é responsável por extrair dados de um endpoint específico:
- **`base_extractor.py`**: Classe abstrata base que define a interface comum:
  - Inicialização com cliente e storage
  - Método `extract()` abstrato (deve ser implementado)
  - Método `extract_and_save()` que orquestra extração + upload
- **Extractors específicos**: Cada um implementa a lógica de extração do seu endpoint:
  - `games_extractor.py`
  - `game_player_stats_extractor.py`
  - `season_averages_extractor.py`
  - `team_season_averages_extractor.py`
  - `active_players_extractor.py`
  - `player_injuries_extractor.py`
  - `team_standings_extractor.py`
  - `player_props_extractor.py`

#### `src/storage/` - Armazenamento
- **`gcs_storage.py`**: Gerencia uploads no Google Cloud Storage:
  - Upload de JSONs
  - Gerenciamento de caminhos seguindo estrutura definida
  - Criação automática de estrutura de pastas
  - Suporte a Application Default Credentials (ADC)

#### `src/bigquery/` - BigQuery
- **`bigquery_client.py`**: Gerencia external tables no BigQuery:
  - Criação de datasets
  - Criação/atualização de external tables
  - Geração de URIs do GCS
  - Autodetect automático de schemas (sem definição manual)

#### `src/utils/` - Utilitários
- **`logger.py`**: Configuração centralizada de logging:
  - Formatação de logs estruturados
  - Níveis de log configuráveis
  - Compatível com Cloud Logging
- **`helpers.py`**: Funções auxiliares:
  - Validação de datas
  - Validação de estruturas JSON
  - Funções utilitárias gerais

### `cloud_run/` - Cloud Run Services

Cada diretório representa um serviço Cloud Run independente para deploy no GCP:

- **Estrutura de cada serviço**:
  - `main.py`: Handler HTTP do serviço usando `functions_framework` que importa e executa o script correspondente de `scripts/`
  - `requirements.txt`: Dependências específicas do serviço (inclui `functions-framework`)
  - `src/`: Diretório `src/` do projeto raiz (deve ser copiado manualmente ou via interface GCP)
  - `scripts/`: Diretório `scripts/` do projeto raiz (deve ser copiado manualmente ou via interface GCP)
  
- **Características**:
  - Cada serviço é independente e pode ser deployado separadamente
  - Usa `functions_framework` para compatibilidade com Cloud Run
  - **Modularidade**: Os `main.py` importam e reutilizam os scripts originais de `scripts/`, evitando duplicação de código
  - Retorna JSON com status da execução
  - Suporta timeout configurável e escalabilidade automática

### `scripts/` - Scripts de Execução

Scripts para execução local ou em ambientes automatizados:

- **Scripts individuais de extração** (um para cada extractor):
  - `extract_active_players.py`
  - `extract_games.py`
  - `extract_game_player_stats.py`
  - `extract_season_averages.py`
  - `extract_player_injuries.py`
  - `extract_team_standings.py`
  - `extract_player_props.py`
  
  Cada script aceita argumentos de linha de comando e pode ser executado independentemente.

- **Scripts de BigQuery**:
  - `create_bigquery_external_tables.py`: Cria external tables no BigQuery para ler dados do GCS
  - `sql/create_external_tables.sql`: Script SQL alternativo para criar external tables

- **Scripts de deploy**:
  - `deploy_cloud_run.sh`: Script automatizado para deploy de serviços Cloud Run

- **Scripts de carga**:
  - `incremental_load.py`: Preparado para implementação futura de ingestão incremental

### `tests/` - Testes

Estrutura preparada para testes unitários e de integração (a implementar).

## Fluxo de Dados

```
API balldontlie.io
    ↓
BallDontLieClient (src/clients/)
    ↓
Extractor específico (src/extractors/)
    ↓
GCSStorage (src/storage/)
    ↓
Google Cloud Storage
```

1. **Cliente** faz requisições HTTP para a API
2. **Extractor** processa e consolida os dados
3. **Storage** faz upload para o GCS seguindo a estrutura definida
4. Dados ficam disponíveis no bucket para processamento posterior

## Princípios de Design

- **Modularidade**: Cada componente tem responsabilidade única
- **Reutilização**: Lógica comum em classes base
- **Extensibilidade**: Fácil adicionar novos endpoints
- **Separação de Concerns**: Clientes, extractors e storage são independentes
- **Cloud-Ready**: Estrutura otimizada para Cloud Run e Cloud Functions

## Endpoints Implementados

1. **games** - Jogos da temporada (com data)
2. **game_player_stats** - Estatísticas de jogadores por jogo (com data)
3. **season_averages** - Médias da temporada (sem data)
4. **team_season_averages** - Médias da temporada por time (general/advanced) (sem data)
5. **active_players** - Jogadores ativos (sem data)
6. **player_injuries** - Lesões de jogadores (sem data)
7. **team_standings** - Classificação de times (sem data)
8. **player_props** - Props de jogadores (DraftKings, todas as prop types) (com data)

## Estrutura de Caminhos no GCS

- **Bucket**: `smartbetting-landing`
- **Endpoints sem data**: `nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}.json`
  - Exemplo: `nba/active_players/2025/raw_nba_active_players_2025.json`
- **Endpoints com data**: `nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{YYYY-MM-DD}.json`
  - Exemplo: `nba/game_player_stats/2025/raw_nba_game_player_stats_2025-10-21.json`
- **Endpoints com category/type** (season_averages, team_season_averages): `nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{category}-{type}.json`
  - Exemplo: `nba/team_season_averages/2025/raw_nba_team_season_averages_2025-general-advanced.json`

## Configuração

### Variáveis de Ambiente

Crie um arquivo `.env` na raiz do projeto com as seguintes variáveis:

```bash
# API Configuration
BALLDONTLIE_KEY=your_api_key_here

# GCS Configuration
GCS_BUCKET_NAME=smartbetting-landing
GCP_PROJECT_ID=your-gcp-project-id

# Season Configuration
SEASON=2025

# Logging Configuration (optional)
LOG_LEVEL=INFO
```

### Instalação de Dependências

```bash
pip install -r requirements.txt
```

### Autenticação no GCP

O projeto usa Application Default Credentials (ADC) para autenticação no GCP. Configure as credenciais:

```bash
gcloud auth application-default login
```

**Nota sobre GCP_PROJECT_ID**: Se você especificar `GCP_PROJECT_ID` no `.env`, o código usará esse projeto explicitamente. Se não especificar, o cliente do GCS usará o projeto padrão das suas credenciais ADC.

## Uso

### Carga Total Inicial

Para fazer a carga total de todos os endpoints da temporada 2025:

```bash
python scripts/full_load.py
```

Ou especificando uma temporada diferente:

```bash
python scripts/full_load.py --season 2024
```

### Uso Individual dos Extractors

Você pode usar scripts individuais para cada endpoint:

```bash
# Jogadores ativos
python scripts/extract_active_players.py --season 2025

# Jogos
python scripts/extract_games.py --season 2025
python scripts/extract_games.py --season 2025 --dates 2025-10-21 2025-10-22
python scripts/extract_games.py --season 2025 --team-ids 1 2 3

# Estatísticas de jogadores por jogo
python scripts/extract_game_player_stats.py --season 2025 --date 2025-10-21
python scripts/extract_game_player_stats.py --season 2025 --game-ids 123 456

# Médias da temporada
python scripts/extract_season_averages.py --season 2025
python scripts/extract_season_averages.py --season 2025 --player-ids 1 2 3

# Médias da temporada por time (general/advanced)
python scripts/extract_team_season_averages.py

# Lesões de jogadores
python scripts/extract_player_injuries.py --season 2025

# Classificação de times
python scripts/extract_team_standings.py --season 2025

# Player Props (DraftKings)
python scripts/extract_player_props.py --season 2025
python scripts/extract_player_props.py --season 2025 --date 2025-10-21
python scripts/extract_player_props.py --season 2025 --prop-types points rebounds assists
```

Ou usar programaticamente:

```python
from src.extractors.active_players_extractor import ActivePlayersExtractor

extractor = ActivePlayersExtractor(season=2025)
gcs_path = extractor.extract_and_save()
print(f"Dados salvos em: {gcs_path}")
```

### Cloud Run Services

#### Pré-requisitos

Antes de fazer o deploy, certifique-se de ter:

1. **gcloud CLI instalado e configurado**:
   ```bash
   # Verificar instalação
   gcloud --version
   
   # Autenticar
   gcloud auth login
   
   # Configurar projeto padrão (opcional)
   gcloud config set project YOUR_PROJECT_ID
   ```

2. **Application Default Credentials configuradas**:
   ```bash
   gcloud auth application-default login
   ```

3. **Arquivo `.env` configurado** na raiz do projeto com as variáveis necessárias:
   ```bash
   BALLDONTLIE_KEY=your_api_key_here
   GCS_BUCKET_NAME=smartbetting-landing
   GCP_PROJECT_ID=your-gcp-project-id
   SEASON=2025
   LOG_LEVEL=INFO
   ```

#### Deploy usando o Script Automatizado

O script `scripts/deploy_cloud_run.sh` automatiza o deploy de todos os serviços ou de um serviço específico.

**Deploy de todos os serviços**:
```bash
./scripts/deploy_cloud_run.sh
```

**Deploy de um serviço específico**:
```bash
./scripts/deploy_cloud_run.sh extract-active-players
```

O script:
- Lê automaticamente as variáveis de ambiente do arquivo `.env`
- Prepara os diretórios necessários (copia `src/` e `scripts/` automaticamente)
- Faz deploy de cada serviço com as configurações padrão:
  - Região: `us-east1`
  - Memória: `512Mi`
  - CPU: `1`
  - Timeout: `3600s` (60 minutos - máximo permitido pelo Cloud Run)
  - Autenticação: Requerida (não permite acesso não autenticado)
- Exibe o status e URL de cada serviço após o deploy

**Serviços disponíveis**:
- `extract-active-players`
- `extract-games`
- `extract-game-player-stats`
- `extract-season-averages`
- `extract-team-season-averages`
- `extract-player-injuries`
- `extract-team-standings`
- `extract-player-props`

#### Deploy Manual de um Serviço

Se preferir fazer deploy manualmente:

```bash
# Navegue até o diretório do serviço
cd cloud_run/extract_active_players

# Copie src/ e scripts/ se necessário
cp -r ../../src .
cp -r ../../scripts .

# Deploy usando gcloud
gcloud run deploy extract-active-players \
  --source . \
  --region us-east1 \
  --platform managed \
  --no-allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 3600 \
  --set-env-vars BALLDONTLIE_KEY=your_key,GCS_BUCKET_NAME=smartbetting-landing,GCP_PROJECT_ID=your-project-id,SEASON=2025,LOG_LEVEL=INFO \
  --project your-project-id
```

#### Verificar Status dos Serviços

**Listar todos os serviços**:
```bash
gcloud run services list --region us-east1 --project YOUR_PROJECT_ID
```

**Ver detalhes de um serviço específico**:
```bash
gcloud run services describe extract-active-players \
  --region us-east1 \
  --project YOUR_PROJECT_ID
```

**Ver logs de um serviço**:
```bash
gcloud run services logs read extract-active-players \
  --region us-east1 \
  --project YOUR_PROJECT_ID
```

#### Testar os Serviços

Após o deploy, você pode testar os serviços. Como a autenticação é requerida, você precisa obter um token de autenticação:

```bash
# Obter token de autenticação
TOKEN=$(gcloud auth print-identity-token)

# Obter URL do serviço
SERVICE_URL=$(gcloud run services describe extract-active-players \
  --region us-east1 \
  --project YOUR_PROJECT_ID \
  --format="value(status.url)")

# Fazer requisição autenticada
curl -X POST "$SERVICE_URL" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json"
```

#### Atualizar Variáveis de Ambiente

**Via UI do GCP**:

1. Acesse o [Cloud Run Console](https://console.cloud.google.com/run)
2. Selecione o serviço que deseja atualizar
3. Clique em **"Edit & Deploy New Revision"**
4. Vá para a aba **"Variables & Secrets"**
5. Edite ou adicione variáveis de ambiente
6. Clique em **"Deploy"**

**Via CLI**:

```bash
# Atualizar uma ou mais variáveis
gcloud run services update extract-active-players \
  --region us-east1 \
  --update-env-vars "SEASON=2026,LOG_LEVEL=DEBUG" \
  --project YOUR_PROJECT_ID

# Atualizar todas as variáveis do .env
# (você precisaria construir o comando manualmente ou usar o script)
```

**Nota**: Atualizar variáveis de ambiente cria uma nova revisão do serviço, mas é mais rápido que um redeploy completo.

#### Estrutura de um Serviço Cloud Run

Cada serviço Cloud Run:
- Recebe requisições HTTP (POST ou GET)
- Importa e executa a função `main()` do script correspondente em `scripts/`
- O script original executa a extração usando a configuração padrão (SEASON do config)
- Faz upload dos dados para o GCS
- Retorna JSON com status da execução

**Vantagens da abordagem modular**:
- **Sem duplicação de código**: A lógica de extração fica apenas nos scripts originais
- **Manutenção simplificada**: Alterações nos scripts são automaticamente refletidas no Cloud Run
- **Consistência**: Mesma lógica para execução local e Cloud Run
- **Mesmo arquivo `.env`**: Reutiliza o mesmo arquivo de configuração usado localmente

**Nota**: Os serviços atuais usam a configuração padrão (variável de ambiente `SEASON`). Para adicionar suporte a parâmetros via request, você pode modificar a função `main()` nos scripts originais ou criar wrappers nos `main.py` do Cloud Run.

#### Exemplos de Requisições

Os serviços atuais usam a configuração padrão (variável de ambiente `SEASON`). Qualquer requisição HTTP aciona a extração:

```bash
# Jogadores ativos
curl -X POST https://extract-active-players-xxx.run.app

# Jogos
curl -X POST https://extract-games-xxx.run.app

# Estatísticas de jogadores
curl -X POST https://extract-game-player-stats-xxx.run.app

# Médias da temporada
curl -X POST https://extract-season-averages-xxx.run.app

# Lesões de jogadores
curl -X POST https://extract-player-injuries-xxx.run.app

# Classificação de times
curl -X POST https://extract-team-standings-xxx.run.app

# Props de jogadores
curl -X POST https://extract-player-props-xxx.run.app
```

**Nota**: Para adicionar suporte a parâmetros customizados (season, dates, etc.), modifique a função `main()` em cada `main.py` para ler do objeto `request`.

#### Resposta dos Serviços

Todos os serviços retornam JSON no seguinte formato:

**Sucesso**:
```json
{
  "status": "success",
  "message": "Extração concluída com sucesso",
  "season": 2025,
  "gcs_path": "nba/active_players/2025/raw_nba_active_players_2025.json"
}
```

**Múltiplos arquivos**:
```json
{
  "status": "success",
  "message": "Extração concluída com sucesso",
  "season": 2025,
  "files_count": 3,
  "gcs_paths": [
    "nba/games/2025/raw_nba_games_2025-2025-10-21.json",
    "nba/games/2025/raw_nba_games_2025-2025-10-22.json",
    "nba/games/2025/raw_nba_games_2025-2025-10-23.json"
  ]
}
```

**Erro**:
```json
{
  "status": "error",
  "message": "Descrição do erro",
  "error_type": "Exception"
}
```

#### Troubleshooting

**Erro: "gcloud CLI não está instalado"**
- Instale o gcloud CLI: https://cloud.google.com/sdk/docs/install

**Erro: "Você não está autenticado no gcloud"**
```bash
gcloud auth login
gcloud auth application-default login
```

**Erro: "Arquivo .env não encontrado"**
- Certifique-se de que o arquivo `.env` existe na raiz do projeto
- Verifique se contém todas as variáveis obrigatórias: `BALLDONTLIE_KEY`, `GCS_BUCKET_NAME`, `GCP_PROJECT_ID`

**Erro: "Permission denied" ao executar o script**
```bash
chmod +x scripts/deploy_cloud_run.sh
```

**Erro: "Service account does not have permission"**
- Verifique se a conta de serviço do Cloud Run tem permissões para:
  - `storage.objects.create` (para upload no GCS)
  - `storage.buckets.get` (para acessar o bucket)

**Serviço não responde ou retorna erro 500**
- Verifique os logs: `gcloud run services logs read SERVICE_NAME --region us-east1`
- Verifique se as variáveis de ambiente estão configuradas corretamente
- Verifique se o bucket do GCS existe e está acessível

#### Configuração de Permissões IAM

Durante o deploy e execução dos serviços Cloud Run, diferentes service accounts são usadas:

**1. Builder (Build Time)** - Service accounts usadas durante o build/deploy:
- **Default Compute Service** (`PROJECT_NUMBER-compute@developer.gserviceaccount.com`)
- **Cloud Build Service Account** (`PROJECT_NUMBER@cloudbuild.gserviceaccount.com`)

**2. Runtime (Execution Time)** - Service account usada quando o serviço está executando:
- **ExtractScripts** (`ExtractScripts@PROJECT_ID.iam.gserviceaccount.com`)

**Configurar todas as permissões necessárias:**

```bash
# Substitua YOUR_PROJECT_ID pelo seu project ID
PROJECT_ID="YOUR_PROJECT_ID"

# Obter project number
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

echo "Configurando permissões para Builder..."

# 1. Default Compute Service (Builder)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/storage.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.editor"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/logging.logWriter"

# 2. Cloud Build Service Account (também usada durante o build)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/storage.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

echo "Configurando permissões para Runtime..."

# 3. ExtractScripts (Runtime)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:ExtractScripts@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

echo "✅ Todas as permissões configuradas!"
```

**Verificar permissões configuradas:**

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)")

# Verificar Default Compute Service
gcloud projects get-iam-policy YOUR_PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --format="table(bindings.role)"

# Verificar Cloud Build SA
gcloud projects get-iam-policy YOUR_PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --format="table(bindings.role)"

# Verificar ExtractScripts
gcloud projects get-iam-policy YOUR_PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:ExtractScripts@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --format="table(bindings.role)"
```

**Troubleshooting de Permissões:**

- **Erro: "storage.objects.get access denied" durante build**
  - Execute os comandos da seção "Builder" acima

- **Erro: "Permission 'artifactregistry.repositories.downloadArtifacts' denied"**
  - Adicione `roles/artifactregistry.writer` à Cloud Build SA (já incluído no script acima)

- **Erro: "does not have permission to write logs to Cloud Logging"**
  - Adicione `roles/logging.logWriter` às service accounts de build (já incluído no script acima)
  - Aguarde 1-2 minutos para propagação das permissões

- **Erro: "Permission denied" ao fazer upload no GCS durante runtime**
  - Execute o comando da seção "Runtime" acima para adicionar `roles/storage.objectAdmin` à ExtractScripts

#### Agendamento com Cloud Scheduler

Você pode agendar execuções periódicas usando Cloud Scheduler:

```bash
# Cria um job do Cloud Scheduler para executar diariamente
gcloud scheduler jobs create http extract-active-players-daily \
  --location=us-central1 \
  --schedule="0 2 * * *" \
  --uri="https://extract-active-players-xxx.run.app" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"season": 2025}' \
  --time-zone="America/Sao_Paulo"
```

#### Monitoramento e Logs

- **Logs**: Acesse os logs no Cloud Logging do GCP
- **Métricas**: Monitore requisições, latência e erros no Cloud Run console
- **Alertas**: Configure alertas para falhas ou alta latência

### Cloud Functions (Legado)

**Nota**: Esta seção descreve o deploy usando Cloud Functions (1ª geração). Para novos deploys, recomenda-se usar Cloud Run (seção acima).

Para deploy das Cloud Functions, você precisa copiar o diretório `src/` para dentro de cada função:

```bash
# Script helper para preparar deploy
for func_dir in cloud_functions/extract_*; do
    cp -r src "$func_dir/"
done
```

Deploy individual:

```bash
gcloud functions deploy extract_active_players \
  --runtime python311 \
  --trigger-http \
  --entry-point extract_active_players \
  --source cloud_functions/extract_active_players \
  --set-env-vars BALLDONTLIE_KEY=your_key,GCS_BUCKET_NAME=smartbetting-landing,GCP_PROJECT_ID=your-project-id,SEASON=2025 \
  --allow-unauthenticated
```

## BigQuery External Tables

Após a extração e armazenamento dos dados no GCS, você pode criar external tables no BigQuery para consultar os dados diretamente sem precisar carregá-los no BigQuery. As external tables leem os arquivos JSON do GCS em tempo real.

### Visão Geral

As external tables permitem:
- **Consultar dados diretamente do GCS** sem duplicar dados no BigQuery
- **Economia de custos** (não há armazenamento duplicado)
- **Atualização automática** quando novos arquivos são adicionados ao GCS
- **Query em tempo real** dos dados brutos

### Estrutura das External Tables

- **Projeto**: `smartbetting-dados`
- **Dataset**: `nba` (criado automaticamente na região `us-east1`)
- **Tabelas**: Uma tabela por endpoint e temporada
  - Formato: `raw_{endpoint}_{season}`
  - Exemplo: `raw_active_players_2025`, `raw_games_2025`

### Endpoints e Estrutura

**Endpoints sem data** (arquivo único):
- `raw_active_players`
- `raw_season_averages_{category}_{type}` (ex: `raw_season_averages_general_advanced`)
- `raw_team_season_averages_{category}_{type}` (ex: `raw_team_season_averages_general_advanced`)
- `raw_player_injuries`
- `raw_team_standings`

**Endpoints com data** (múltiplos arquivos com wildcard):
- `raw_games_{season}` - Lê todos os arquivos `nba/games/{season}/*.json`
- `raw_game_player_stats_{season}` - Lê todos os arquivos `nba/game_player_stats/{season}/*.json`
- `raw_player_props_{season}` - Lê todos os arquivos `nba/player_props/{season}/*/*.json`

### Pré-requisitos

1. **Biblioteca do BigQuery instalada**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Autenticação no GCP**:
   ```bash
   gcloud auth application-default login
   ```

3. **Permissões no BigQuery**:
   - A conta de serviço precisa de permissões para criar datasets e tabelas
   - Permissão: `roles/bigquery.dataEditor` ou `roles/bigquery.admin`

### Criar External Tables

#### Opção 1: Script Python (Recomendado)

Use o script Python para criar todas as external tables automaticamente:

```bash
# Criar todas as external tables para a temporada configurada no .env
python scripts/create_bigquery_external_tables.py
```

O script:
- Usa o módulo `src/bigquery/BigQueryClient` para encapsular a lógica
- Cria o dataset `nba` no projeto `smartbetting-dados` (região `us-east1`) se não existir
- Cria/atualiza todas as external tables configuradas
- Usa autodetect do BigQuery para inferir schemas automaticamente (sem definição manual)
- Suporta wildcards para endpoints com múltiplos arquivos
- Usa a temporada definida em `SEASON` no arquivo `.env` (padrão: 2025)

#### Opção 2: Script SQL

Use o script SQL para criar as tabelas manualmente:

1. Edite `scripts/sql/create_external_tables.sql` e substitua as variáveis:
   - `${GCS_BUCKET_NAME}`: Nome do bucket (padrão: `smartbetting-landing`)
   - `${SEASON}`: Temporada (ex: `2025`)

2. Execute no BigQuery Console ou via CLI:
   ```bash
   bq query --use_legacy_sql=false < scripts/sql/create_external_tables.sql
   ```

**Nota**: O script SQL já está configurado com:
- Projeto: `smartbetting-dados`
- Dataset: `nba`
- Região: `us-east1`

### Consultar Dados

Após criar as external tables, você pode consultar os dados normalmente no BigQuery:

```sql
-- Consultar jogadores ativos
SELECT 
  season,
  total_players,
  players
FROM `smartbetting-dados.nba.raw_active_players_2025`;

-- Consultar jogos (com dados aninhados)
SELECT 
  season,
  date,
  total_games,
  game.id,
  game.date as game_date,
  game.home_team.full_name as home_team,
  game.visitor_team.full_name as visitor_team,
  game.home_team_score,
  game.visitor_team_score
FROM `smartbetting-dados.nba.raw_games_2025`,
UNNEST(games) as game
WHERE game.date = '2025-10-21'
ORDER BY game.id;

-- Consultar estatísticas de jogadores por jogo
SELECT 
  season,
  date,
  stat.player.first_name,
  stat.player.last_name,
  stat.pts,
  stat.reb,
  stat.ast
FROM `smartbetting-dados.nba.raw_game_player_stats_2025`,
UNNEST(game_player_stats) as stat
WHERE date = '2025-10-21'
ORDER BY stat.pts DESC
LIMIT 10;
```

### Estrutura dos Dados

Os dados JSON salvos no GCS têm a seguinte estrutura:

**Endpoints sem data**:
```json
{
  "season": 2025,
  "total_players": 500,
  "players": [...]
}
```

**Endpoints com data**:
```json
{
  "season": 2025,
  "date": "2025-10-21",
  "total_games": 10,
  "games": [...]
}
```

O BigQuery automaticamente detecta e estrutura esses dados em tabelas relacionais, permitindo consultas SQL normais.

### Atualização Automática

As external tables são atualizadas automaticamente quando:
- Novos arquivos são adicionados ao GCS (para endpoints com wildcard)
- Os arquivos existentes são modificados

**Nota**: Para endpoints com wildcard (games, game_player_stats, player_props), novos arquivos adicionados ao GCS aparecem automaticamente nas consultas sem necessidade de atualizar a tabela.

### Troubleshooting

**Erro: "Access Denied" ao criar tabelas**
- Verifique se a conta de serviço tem permissões `bigquery.datasets.create` e `bigquery.tables.create`
- Execute: `gcloud projects add-iam-policy-binding PROJECT_ID --member="serviceAccount:..." --role="roles/bigquery.dataEditor"`

**Erro: "File not found" ao consultar**
- Verifique se os arquivos existem no GCS no caminho especificado
- Verifique se o bucket está acessível
- Use: `gsutil ls gs://smartbetting-landing/nba/{endpoint}/{season}/`

**Erro: "Invalid JSON"**
- Verifique se os arquivos JSON no GCS são válidos
- Use: `gsutil cat gs://smartbetting-landing/nba/{endpoint}/{season}/arquivo.json | python -m json.tool`

**Tabela não mostra novos arquivos**
- Para endpoints com wildcard, os novos arquivos devem aparecer automaticamente
- Verifique se os novos arquivos seguem o padrão de nomenclatura esperado
- Recrie a tabela se necessário: `python scripts/create_bigquery_external_tables.py`

**Performance lenta em consultas**
- External tables podem ser mais lentas que tabelas nativas do BigQuery
- Considere criar tabelas materializadas para consultas frequentes
- Use filtros apropriados para reduzir dados processados

### Próximos Passos

Após configurar as external tables, você pode:
1. **Criar views** para simplificar consultas comuns
2. **Criar tabelas materializadas** para melhor performance em consultas frequentes
3. **Configurar transformações** para limpar e estruturar os dados
4. **Criar dashboards** no Data Studio ou Looker usando os dados do BigQuery

## Características Técnicas

- **Paginação Automática**: Todos os endpoints suportam paginação e são processados automaticamente
- **Logging Estruturado**: Logs formatados para Cloud Logging
- **Tratamento de Erros**: Tratamento de erros com logging detalhado
- **Validação de Dados**: Validação básica de estrutura JSON antes do upload

## Ingestão Incremental (Futuro)

A estrutura está preparada para implementação de ingestão incremental:
- Armazenamento de timestamps de última execução
- Detecção de novos dados baseada em datas
- Cloud Scheduler para execução periódica
- Pub/Sub para triggers baseados em eventos

O script `scripts/incremental_load.py` está preparado para essa funcionalidade.

## Desenvolvimento

### Estrutura Modular

O código é organizado de forma modular para facilitar:
- Manutenção
- Testes
- Deploy individual de Cloud Functions
- Reutilização de componentes

### Adicionar Novo Endpoint

1. Adicione configuração em `src/config.py` (ENDPOINT_CONFIGS)
2. Crie método no `BallDontLieClient` (`src/clients/balldontlie_client.py`)
3. Crie extractor em `src/extractors/` herdando de `BaseExtractor`
4. Crie script em `scripts/` para execução local
5. Crie serviço Cloud Run em `cloud_run/`:
   - Crie diretório `cloud_run/extract_novo_endpoint/`
   - Crie `main.py` seguindo o padrão dos outros serviços
   - Crie `requirements.txt` copiando de outro serviço
   - Execute `prepare_services.sh` para copiar `src/`
6. Adicione ao script `full_load.py` se necessário

## Troubleshooting

### Erro de Autenticação no GCP

Certifique-se de que as credenciais ADC estão configuradas:
```bash
gcloud auth application-default login
```

### Erro de Permissão no Bucket

Verifique se a conta de serviço tem permissões para:
- `storage.objects.create`
- `storage.buckets.get`
- `storage.buckets.create` (se o bucket não existir)

### Rate Limiting da API

Se você encontrar problemas de rate limiting:
- Aumente o delay entre requisições (já implementado na paginação)
- Reduza `per_page` nas configurações
- Execute em horários de menor tráfego
- Implemente seu próprio mecanismo de retry se necessário

## Licença

Este projeto é privado e proprietário.

