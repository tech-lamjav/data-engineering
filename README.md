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
│   │   ├── active_players_extractor.py
│   │   ├── player_injuries_extractor.py
│   │   ├── team_standings_extractor.py
│   │   └── player_props_extractor.py
│   ├── storage/                 # Armazenamento
│   │   └── gcs_storage.py       # Gerenciamento de uploads no GCS
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
│   ├── full_load.py            # Carga total inicial
│   └── incremental_load.py     # Ingestão incremental (futuro)
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
  - `main.py`: Handler HTTP do serviço usando `functions_framework`
  - `requirements.txt`: Dependências específicas do serviço (inclui `functions-framework`)
  - `src/`: Diretório `src/` do projeto raiz (deve ser copiado manualmente ou via interface GCP)
  
- **Características**:
  - Cada serviço é independente e pode ser deployado separadamente
  - Usa `functions_framework` para compatibilidade com Cloud Run
  - Retorna JSON com status da execução e caminho(s) do(s) arquivo(s) no GCS
  - Suporta timeout configurável e escalabilidade automática
  - Estrutura simplificada seguindo padrão de funções wrapper

### `scripts/` - Scripts de Execução

Scripts para execução local ou em ambientes automatizados:

- **Scripts individuais** (um para cada extractor):
  - `extract_active_players.py`
  - `extract_games.py`
  - `extract_game_player_stats.py`
  - `extract_season_averages.py`
  - `extract_player_injuries.py`
  - `extract_team_standings.py`
  - `extract_player_props.py`
  
  Cada script aceita argumentos de linha de comando e pode ser executado independentemente.

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
4. **active_players** - Jogadores ativos (sem data)
5. **player_injuries** - Lesões de jogadores (sem data)
6. **team_standings** - Classificação de times (sem data)
7. **player_props** - Props de jogadores (DraftKings, todas as prop types) (com data)

## Estrutura de Caminhos no GCS

- **Bucket**: `smartbetting-landing`
- **Endpoints sem data**: `nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}.json`
  - Exemplo: `nba/active_players/2025/raw_nba_active_players_2025.json`
- **Endpoints com data**: `nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{YYYY-MM-DD}.json`
  - Exemplo: `nba/game_player_stats/2025/raw_nba_game_player_stats_2025-10-21.json`

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

**Importante**: Para deploy dos serviços Cloud Run, você precisa incluir o diretório `src/` junto com os arquivos de cada serviço. O `main.py` está configurado para encontrar o `src/` no diretório raiz do projeto.

#### Preparação dos Arquivos

Cada serviço precisa ter:
- `main.py`: Handler HTTP do serviço
- `requirements.txt`: Dependências do serviço
- `src/`: Diretório completo `src/` do projeto (copie manualmente ou via interface GCP)

**Opção 1 - Via Interface GCP**: Ao fazer upload dos arquivos na interface do Cloud Run, inclua:
- O arquivo `main.py` do serviço
- O arquivo `requirements.txt` do serviço
- Todo o diretório `src/` do projeto raiz

**Opção 2 - Via CLI**: Copie o `src/` para cada serviço antes do deploy:

```bash
# Para cada serviço, copie o src/
cp -r src cloud_run/extract_active_players/
cp -r src cloud_run/extract_games/
# ... e assim por diante
```

#### Deploy Individual de um Serviço

Para fazer deploy de um serviço específico:

```bash
# Navegue até o diretório do serviço
cd cloud_run/extract_active_players

# Deploy usando gcloud
gcloud run deploy extract-active-players \
  --source . \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars BALLDONTLIE_KEY=your_key,GCS_BUCKET_NAME=smartbetting-landing,GCP_PROJECT_ID=your-project-id,SEASON=2025 \
  --timeout 540 \
  --memory 512Mi \
  --max-instances 10
```

**Parâmetros importantes**:
- `--source .`: Usa o diretório atual como fonte
- `--region`: Escolha a região mais próxima (ex: `us-central1`, `us-east1`, `southamerica-east1`)
- `--timeout 540`: Timeout de 9 minutos (540 segundos) - ajuste conforme necessário
- `--memory 512Mi`: Memória alocada - aumente se necessário
- `--max-instances 10`: Limite de instâncias simultâneas

#### Deploy de Todos os Serviços

Você pode criar um script para fazer deploy de todos os serviços:

```bash
#!/bin/bash
# deploy_all.sh

REGION="us-central1"
PROJECT_ID="your-project-id"

SERVICES=(
    "extract-active-players"
    "extract-games"
    "extract-game-player-stats"
    "extract-season-averages"
    "extract-player-injuries"
    "extract-team-standings"
    "extract-player-props"
)

for service in "${SERVICES[@]}"; do
    service_dir=$(echo $service | sed 's/-/_/g')
    echo "Deploying $service..."
    
    cd "cloud_run/$service_dir"
    
    gcloud run deploy "$service" \
      --source . \
      --region "$REGION" \
      --platform managed \
      --allow-unauthenticated \
      --set-env-vars BALLDONTLIE_KEY=$BALLDONTLIE_KEY,GCS_BUCKET_NAME=smartbetting-landing,GCP_PROJECT_ID=$PROJECT_ID,SEASON=2025 \
      --timeout 540 \
      --memory 512Mi \
      --max-instances 10
    
    cd ../..
done
```

#### Estrutura de um Serviço Cloud Run

Cada serviço Cloud Run:
- Recebe requisições HTTP (POST ou GET)
- Executa a extração do endpoint correspondente usando a configuração padrão (SEASON do config)
- Faz upload dos dados para o GCS
- Retorna JSON com status da execução e caminho(s) do(s) arquivo(s)

**Nota**: Os serviços atuais usam a configuração padrão (variável de ambiente `SEASON`). Para adicionar suporte a parâmetros via request, você pode modificar a função `main()` em cada `main.py`.

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

#### Configuração de Variáveis de Ambiente

Configure as variáveis de ambiente no Cloud Run através do console ou CLI:

```bash
gcloud run services update extract-active-players \
  --region us-central1 \
  --update-env-vars BALLDONTLIE_KEY=your_key,GCS_BUCKET_NAME=smartbetting-landing,SEASON=2025
```

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

