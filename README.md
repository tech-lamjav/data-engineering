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
├── cloud_functions/             # Cloud Functions
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

### `cloud_functions/` - Cloud Functions

Cada diretório representa uma Cloud Function independente para deploy no GCP:

- **Estrutura de cada função**:
  - `main.py`: Handler HTTP da função
  - `requirements.txt`: Dependências específicas da função
  
- **Características**:
  - Cada função é independente e pode ser deployada separadamente
  - Importa módulos do `src/` (que deve ser copiado durante deploy)
  - Aceita parâmetros via JSON no body da requisição
  - Retorna status da execução e caminho do arquivo no GCS

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
- **Cloud-Ready**: Estrutura otimizada para Cloud Functions

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

### Cloud Functions

**Importante**: Para deploy das Cloud Functions, você precisa copiar o diretório `src/` para dentro de cada função, ou usar uma estrutura de empacotamento. 

#### Opção 1: Copiar src/ para cada função (recomendado para deploy)

```bash
# Script helper para preparar deploy
for func_dir in cloud_functions/extract_*; do
    cp -r src "$func_dir/"
done
```

#### Opção 2: Deploy individual

```bash
# Deploy de uma função específica
gcloud functions deploy extract_active_players \
  --runtime python311 \
  --trigger-http \
  --entry-point extract_active_players \
  --source cloud_functions/extract_active_players \
  --set-env-vars BALLDONTLIE_KEY=your_key,GCS_BUCKET_NAME=smartbetting-landing,GCP_PROJECT_ID=your-project-id,SEASON=2025 \
  --allow-unauthenticated
```

#### Opção 3: Usar Cloud Build

Crie um `cloudbuild.yaml` para automatizar o deploy de todas as funções.

#### Estrutura de uma Cloud Function

Cada Cloud Function:
- Recebe requisições HTTP (POST ou GET)
- Aceita parâmetros opcionais via JSON body
- Executa a extração do endpoint correspondente
- Faz upload dos dados para o GCS
- Retorna status da execução

Exemplo de requisição:

```bash
curl -X POST https://your-region-your-project.cloudfunctions.net/extract_active_players \
  -H "Content-Type: application/json" \
  -d '{"season": 2025}'
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
4. Crie Cloud Function em `cloud_functions/`
5. Adicione ao script `full_load.py` se necessário

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

