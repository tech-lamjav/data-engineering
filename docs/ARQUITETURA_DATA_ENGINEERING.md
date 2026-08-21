# Arquitetura do `data-engineering` — Extração de Endpoints e Infraestrutura GCP

> Documento de referência da camada de **ingestão** do Smartbetting. Cobre **como extraímos
> os endpoints** das APIs externas e **a infraestrutura GCP** que orquestra, armazena e serve
> esses dados. É o documento guarda-chuva; para detalhes verticais ver
> [`PIPELINE_APIFOOTBALL.md`](PIPELINE_APIFOOTBALL.md) (futebol) e
> [`EXPANSAO_CAMPEONATOS_APIFOOTBALL.md`](EXPANSAO_CAMPEONATOS_APIFOOTBALL.md) (novas ligas).
>
> Última revisão: 2026-06-30.

---

## 1. Visão geral

O `data-engineering/` é um pipeline modular de **extração → landing → external tables** que
alimenta duas verticais de dados esportivos, partilhando a mesma base de código:

| Vertical | API de origem | Dataset BigQuery | Produto |
|---|---|---|---|
| **NBA** | [balldontlie.io](https://docs.balldontlie.io/) (v1/v2 + `nba/v1`/`nba/v2`) | `nba` | Player props / props de jogador |
| **Futebol** | [API-Football v3](https://www.api-football.com) (`v3.football.api-sports.io`) | `futebol` | *Value betting* (Brasileirão + Copa do Mundo) |

Princípios que valem para **todo** o repositório (ver [`.cursorrules`](../.cursorrules) e
[`CLAUDE.md`](../CLAUDE.md)):

- **Lógica de negócio só em `src/`.** `scripts/` são orquestradores finos; `cloud_run/*/main.py`
  são wrappers HTTP. O **mesmo código** roda local e na nuvem.
- **Sem `argparse`.** Toda configuração vem de `src/config.py` + variáveis de ambiente.
- **Parametrização total.** O futebol é 100% dirigido por `(league_id, season)` em `src/config.py`;
  adicionar uma liga é editar config + 2 `CASE` no dbt, **zero código de extrator novo**.
- **Não-breaking entre verticais.** A base nasceu acoplada à NBA; o futebol foi adicionado
  generalizando (`sport=`, `auth_headers=`) sem quebrar os defaults NBA.

### 1.1 Fluxo de dados ponta a ponta

```
   API externa (balldontlie / API-Football)
        │   HTTP GET (retry + paginação + detecção de quota)
        ▼
   src/clients/*          BallDontLieClient | ApiFootballClient  (← BaseClient)
        │
        ▼
   src/extractors/*       *Extractor  (← BaseExtractor)
        │   transforma, carimba (loaded_at, season_type, event_order, snapshot_date…)
        ▼
   src/storage/gcs_storage.py    → NDJSON, normaliza chaves p/ BigQuery
        │
        ▼
   GCS  gs://smartbetting-landingzone/{nba|futebol}/{endpoint}/...   (landing zone)
        │
        ▼
   BigQuery EXTERNAL TABLES   smartbetting-dados.{nba|futebol}.raw_*   (lê o GCS em tempo real)
        │
        ▼
   dbt (analytics-engineering)  staging → marts   [fora deste repo]
        │
        ▼
   sync_bq_to_postgres → Supabase Postgres (nba_mart)  → app prop-play-predictor
```

A orquestração (quem dispara o quê, e quando) é feita por **GCP Workflows** acionados pelo
**Cloud Scheduler**; cada extrator é um **serviço Cloud Run** independente. Tudo detalhado nas
seções 5–6.

### 1.2 Identificadores de infra (fonte: `src/config.py` + `.env`)

| Item | Valor |
|---|---|
| Projeto GCP | `smartbetting-dados` (hospeda Cloud Run, Workflows, BigQuery e GCS) |
| Região | `us-east1` (Cloud Run, Workflows, datasets BQ) |
| Bucket de landing | `gs://smartbetting-landingzone` ⚠️ (o default `smartbetting-landing` em `config.py:197` é **bucket morto** — o real vem do `.env`) |
| Datasets BigQuery | `nba`, `futebol`, `sandbox` |
| Postgres de serving | Supabase (`nba_mart`), ambientes PRD e DEV |

---

## 2. Camadas de código (`src/`)

```
src/
├── config.py              # Fonte única de verdade: env vars, endpoints, ligas, janelas, paths GCS
├── clients/
│   ├── base_client.py     # HTTP base: retry, paginação, detecção de quota
│   ├── balldontlie_client.py  # NBA
│   └── api_football_client.py # Futebol
├── extractors/
│   ├── base_extractor.py      # contrato extract() + extract_and_save() + helpers por-data NBA
│   ├── per_fixture_extractor.py # base dos endpoints per-fixture de futebol
│   └── *_extractor.py         # 1 por endpoint (24 concretos)
├── storage/gcs_storage.py # Upload NDJSON + leitura de game_ids/fixture_ids do GCS
├── bigquery/bigquery_client.py # Cria/atualiza external tables NBA
├── sync/bq_to_postgres.py # Materializa marts BQ → Supabase Postgres
├── reporting/daily_summary.py  # Resumo diário de execuções (1 email/dia)
│   └── {api_quota,guardas,procedencia,suite_dbt}.py # 1 seção do e-mail cada (§5.9-5.10)
└── utils/                 # logger + helpers (normalização de chaves p/ BigQuery)
```

`src/config.py` é o coração: além das credenciais, define `ENDPOINT_CONFIGS` (metadados NBA),
as tuplas `*_CURRENT`/`*_BACKFILL` por liga (futebol), as janelas de poll (`FUTEBOL_ODDS_WINDOWS`
etc.), `MART_TABLES_ORDERED` (sync) e o **construtor único de caminhos GCS** `get_gcs_path()`.

---

## 3. Estrutura de extração dos endpoints

### 3.1 O cliente HTTP base — `src/clients/base_client.py`

Toda chamada passa por `BaseClient`, que concentra a resiliência:

- **Autenticação plugável.** `auth_headers` (futebol: `x-apisports-key`) tem precedência sobre
  `Authorization: Bearer` (NBA). É o que permite uma única base servir as duas APIs
  (`base_client.py:59-81`).
- **Retry com backoff exponencial e teto.** Retenta em `429/500/502/503/504` e erros transitórios
  de conexão; backoffs `[30,60,120,240,480]s` limitados a `_MAX_RETRY_WAIT=120s`; respeita
  `Retry-After` (também limitado ao teto, p/ não dormir minutos e estourar o timeout do Cloud Run)
  (`base_client.py:10-15, 83-138`).
- **Paginação dupla.** `get_paginated()` lida com o envelope balldontlie (`{data, meta}`,
  cursor-based **ou** page-based) e valida completude contra `meta.total_count`
  (`base_client.py:159-300`). O envelope da API-Football (`{response, paging:{current,total}}`)
  **não** passa por aqui — os métodos paginados do `ApiFootballClient` iteram `page=1..total`
  manualmente (`/players`, `/odds`).
- **Detecção de estouro de quota (API-Football).** A API-Football **não** devolve 429 quando a
  cota diária estoura: responde `HTTP 200` com `errors` preenchido e `response` vazio. Tratar isso
  como "sem dados" mascararia lacunas — pior nos polls forward-only, que **perdem a janela para
  sempre**. Por isso `is_quota_error()` + `ApiQuotaExceededError` fazem o run **falhar** em vez de
  gravar coleta parcial como sucesso (`base_client.py:18-53`; aplicado em todo método do
  `ApiFootballClient` via `_raise_if_quota`).

### 3.2 Vertical NBA — `BallDontLieClient`

Quatro base URLs convivem (`config.py:11-14`): `v1` (jogos/stats), `v2` (odds/props),
`nba/v1` (team_season_averages), `nba/v2` (advanced stats). Endpoints consumidos:

| Método do client | Endpoint API | Paginação | Observação |
|---|---|---|---|
| `get_games` | `GET /games` | sim | filtro por `seasons[]` + `dates[]` |
| `get_game_player_stats` | `GET /stats` | sim | aceita `period` (1–4) |
| `get_game_player_advanced_stats` | `GET nba/v2/stats/advanced` | sim | dezenas de métricas de tracking |
| `get_season_averages` | `GET /season_averages/{category}` | sim | `category`×`type`×`season_type` |
| `get_team_season_averages` | `GET nba/v1/team_season_averages/{category}` | sim | idem, por time |
| `get_active_players` | `GET /players/active` | cursor | sem `max_items` (não truncar) |
| `get_player_injuries` | `GET /player_injuries` | sim | latest-only |
| `get_team_standings` | `GET /standings` | sim | por temporada |
| `get_player_props` | `GET v2/odds/player_props` | sim | por `game_id` + `vendors[]` |
| `get_betting_odds` | `GET v2/odds` | sim | por `game_id`, todos os vendors |

### 3.3 Vertical Futebol — `ApiFootballClient`

Envelope `{get, parameters, errors, results, paging, response}`. Particularidades por endpoint
(documentadas em `api_football_client.py`):

| Método do client | Endpoint API | Pagina? | Forma do `response` |
|---|---|---|---|
| `get_league` | `GET /leagues?id&season` | não | `[{league, country, seasons:[…coverage]}]` |
| `get_teams` | `GET /teams?league&season` | não | `[{team, venue}]` |
| `get_players` | `GET /players?league&season` | **sim** (`paging.total`) | catálogo de jogadores |
| `get_fixtures` | `GET /fixtures?league&season` | **não** (rejeita `page`) | tabela mãe — temporada inteira |
| `get_fixture_statistics` | `GET /fixtures/statistics?fixture` | não | 2 blocos/jogo `{type,value}` (só após FT) |
| `get_fixture_events` | `GET /fixtures/events?fixture` | não | N eventos (sem ordem própria → `event_order`) |
| `get_fixture_lineups` | `GET /fixtures/lineups?fixture` | não | 2 blocos (startXI/substitutes) |
| `get_fixture_player_stats` | `GET /fixtures/players?fixture` | não | stats por jogador (struct aninhado) |
| `get_team_season_stats` | `GET /teams/statistics?league&season&team` | não | **objeto único** (não lista) |
| `get_standings` | `GET /standings?league&season` | não | `standings` = **array de arrays** (1/grupo) |
| `get_injuries` | `GET /injuries?league&season` | **não** (rejeita `page`) | log da temporada inteira (~2k/liga) |
| `get_injuries_by_fixture` | `GET /injuries?fixture` | não | desfalques de 1 jogo (pré-jogo) |
| `get_odds` | `GET /odds?fixture` | **sim** (merge de `bookmakers[]`) | todas as casas/mercados |
| `get_predictions` | `GET /predictions?fixture` | não | 1 elemento (predictions + comparison) |

### 3.4 Os arquétipos de extração

Todo extrator herda de `BaseExtractor` (contrato `extract()` + `extract_and_save()` →
`storage.upload_json()`). Há **seis padrões** recorrentes — entender os padrões é mais útil que
memorizar os 24 extratores:

1. **Latest-only (NBA)** — 1 arquivo, sem data. `extract_and_save()` puro. Ex.: `ActivePlayers`,
   `PlayerInjuries`, `TeamStandings`, `(Team)SeasonAverages`.
2. **Fato por-data (NBA)** — itera o range da temporada (`_get_season_date_range()`:
   21/out → fim ou hoje-UTC), 1 request e 1 arquivo por dia, via `extract_and_save_by_date()`.
   Distingue "dia sem dados" (skip) de "dia falhou" (ERROR de resumo). Ex.: `Games`,
   `GamePlayerStats`, `GamePlayerAdvancedStats`, `GamePlayerStatsPeriod` (loop data×período).
3. **Fato por-jogo forward (NBA)** — lê os `game_id` já gravados no GCS
   (`get_game_ids_from_storage`), **skip-if-exists** + janela de re-fetch
   (`NBA_PER_GAME_REFETCH_WINDOW_DAYS`): pula jogos antigos já salvos, re-busca os recentes.
   1 arquivo por jogo. Ex.: `BettingOdds`, `PlayerProps` (loop game×vendor).
4. **Catálogo futebol `current`/`backfill`** — itera `(league_id, season)` das tuplas de config
   conforme o `mode`; latest-only, 1 arquivo por modo; **aborta com `raise` se algum alvo falhar**
   (não sobrescreve arquivo bom com coleta parcial). Ex.: `Leagues`, `Teams`, `Players`, `Fixtures`,
   `TeamSeasonStats`.
5. **Per-fixture pós-FT (futebol)** — base `PerFixtureExtractor`: lê fixtures **finalizados**
   (`FT/AET/PEN`) do GCS, skip-if-exists + janela de re-fetch só no `current` (captura correções
   pós-jogo da API), **não grava vazio**, 1 arquivo por fixture. Ex.: `FixtureStatistics`,
   `FixtureEvents`, `FixturePlayerStats`. (`FixtureLineups` tem loop próprio por causa das 2 fases.)
6. **Snapshot diário / poll forward-only (futebol)** — arquivo **date-stampado** (o GCS acumula
   histórico; re-run no mesmo dia sobrescreve = idempotente). Snapshots diários: `Standings`,
   `Injuries` (modo season-log). Polls pré-jogo (lêem jogos **NS** futuros via
   `get_upcoming_fixtures_with_kickoff` e bucketam por proximidade do kickoff): `Odds`
   (T-24h/T-1h/T-15m), `Predictions` (daily/T-2h), `Injuries` (modo `pregame`), `FixtureLineups`
   (modo `pregame`, escalação confirmada ~T-30min). Forward-only = janela perdida não se reconstrói.

### 3.5 Catálogo de extractors

**NBA** (`src/extractors/`, client `BallDontLieClient`):

| Extractor | Endpoint | Padrão | Carimbo/transformação |
|---|---|---|---|
| `ActivePlayersExtractor` | `/players/active` | latest-only | array `players` |
| `PlayerInjuriesExtractor` | `/player_injuries` | latest-only | array `injuries` |
| `TeamStandingsExtractor` | `/standings` | latest-only | array `standings` |
| `SeasonAveragesExtractor` | `/season_averages/{cat}` | latest-only | combo category/type/season_type no path |
| `TeamSeasonAveragesExtractor` | `nba/v1/.../{cat}` | latest-only | idem, por time |
| `GamesExtractor` | `/games` | por-data | remove `status` (estabiliza tipo no BQ) |
| `GamePlayerStatsExtractor` | `/stats` | por-data | `season_type` por jogo |
| `GamePlayerAdvancedStatsExtractor` | `nba/v2/stats/advanced` | por-data | `season_type` por jogo |
| `GamePlayerStatsPeriodExtractor` | `/stats?period` | por-data×período | subdir `q{period}/` |
| `BettingOddsExtractor` | `v2/odds` | por-jogo forward | 1 arquivo/game_id |
| `PlayerPropsExtractor` | `v2/odds/player_props` | por-jogo forward | loop game×vendor; 1 arquivo/(game,vendor) |

**Futebol** (`src/extractors/`, client `ApiFootballClient`, `sport="futebol"`):

| Extractor | Endpoint | Padrão | Nota-chave |
|---|---|---|---|
| `LeaguesExtractor` | `/leagues` | catálogo current/backfill | valida `coverage.*` |
| `TeamsExtractor` | `/teams` | catálogo | fonte do `/teams/statistics` |
| `PlayersExtractor` | `/players` | catálogo (paginado) | dedup fica no dbt |
| `FixturesExtractor` | `/fixtures` | catálogo (tabela mãe) | `fixture_id` destrava o resto |
| `FixtureStatisticsExtractor` | `/fixtures/statistics` | per-fixture pós-FT | stringifica `value` (tipo misto) |
| `FixtureEventsExtractor` | `/fixtures/events` | per-fixture pós-FT | carimba `event_order` |
| `FixturePlayerStatsExtractor` | `/fixtures/players` | per-fixture pós-FT | achata `statistics[0]` |
| `FixtureLineupsExtractor` | `/fixtures/lineups` | pós-FT (`real`) + pré-jogo (`confirmed`) | `lineup_phase`; latest-wins no dbt |
| `TeamSeasonStatsExtractor` | `/teams/statistics` | catálogo (1/time) | `_curate()` para o Poisson |
| `StandingsExtractor` | `/standings` | snapshot diário date-stamped | achata grupos×times |
| `InjuriesExtractor` | `/injuries` (league \| fixture) | snapshot diário + poll pregame | Copa excluída (coverage FALSE) |
| `OddsExtractor` | `/odds` | poll forward-only (3 janelas) | todas as casas; sem date-stamp (skip permanente) |
| `PredictionsExtractor` | `/predictions` | poll forward-only (2 janelas) | só `predictions`+`comparison` |

Bases sem script: `BaseExtractor`, `PerFixtureExtractor`.

### 3.6 Scripts de extração (`scripts/` e `scripts/futebol/`)

Mapeamento **1:1** script ↔ extractor (24 scripts). São finos: importam de `src/`, lêem config,
chamam `extract_and_save()`. Exceções dignas de nota:

- `extract_season_averages.py` / `extract_team_season_averages.py` — fazem **loop** sobre
  `(TEAM_)SEASON_AVERAGES_COMBINATIONS × SEASON_TYPES` (1 instância de extractor por combo).
- `extract_player_props.py` — `main(vendor=None)`; o **split por casa não existe no script**, e sim
  na camada Cloud Run (3 serviços passam `vendor=` ao mesmo `main`).
- Futebol: o modo vem de `get_mode("<X>_MODE")` (`current`/`backfill`/`pregame`).
  `extract_odds.py` e `extract_predictions.py` **não têm modo** (uma passada cobre as janelas).

---

## 4. Armazenamento — GCS landing zone

`src/storage/gcs_storage.py` faz o upload e é também a **fonte de leitura** que os extratores
forward usam para descobrir game_ids/fixture_ids.

- **Construção de caminhos:** sempre via `get_gcs_path()` (`config.py:475-565`) — nunca à mão.
  Os branches cobrem NBA (com/sem data, category/type, market+game_id, período) e futebol
  (`sport="futebol"`: latest-only por modo, date-stamped, e per-fixture com sufixo de fase/janela).
- **NDJSON (newline-delimited).** `_convert_to_newline_delimited_json()` explode o array principal
  em 1 objeto JSON por linha (formato que o BigQuery espera). Quais chaves são "o array" depende do
  endpoint; a lista `array_keys` (`gcs_storage.py:218`) registra as keys de futebol.
  ⚠️ **Gotcha:** um extrator novo que devolva `{minha_key:[...]}` **precisa** ter `minha_key`
  registrada em `array_keys`, senão o payload inteiro vira **uma linha gigante**. (`predictions`
  é a exceção proposital: dicts aninhados caem no fallback de 1 linha.)
- **Normalização de chaves p/ BigQuery.** `normalize_dict_keys()` (`utils/helpers.py`) troca
  caracteres inválidos por `_` recursivamente antes do upload.
- **Sem round-trips supérfluos.** O bucket é referência lazy (não cria bucket em runtime; isso é
  IaC), e o upload não faz `exists()` extra — eram milhares por execução.

### Estrutura de caminhos

```
gs://smartbetting-landingzone/
├── nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}[-{date}].json
│   ├── .../player_props/{season}/{vendor}/raw_nba_player_props_{season}-{game_id}.json
│   ├── .../season_averages/{season}/...-{category}-{type}-{season_type}.json
│   └── .../game_player_stats_period/{season}/q{period}/...-{date}.json
└── futebol/{endpoint}/raw_futebol_{endpoint}{_mode}{_date}.json
    ├── snapshot diário:  raw_futebol_standings_{mode}_{YYYY-MM-DD}.json
    ├── per-fixture odds: raw_futebol_odds_{fixture}_{t24h|t1h|t15m}.json
    └── per-fixture pred: raw_futebol_predictions_{fixture}_daily_{YYYY-MM-DD}.json
```

---

## 5. Infraestrutura Cloud GCP

### 5.1 BigQuery — external tables

Em vez de carregar dados, criamos **external tables** que lêem os NDJSON do GCS em tempo real:
sem duplicação de storage, novos arquivos aparecem sozinhos no glob `*.json`.

- **NBA** (`scripts/create_bigquery_external_tables.py` → `BigQueryClient.create_all_external_tables`):
  um loop sobre `ENDPOINT_CONFIGS` no dataset `nba`, **season-aware**, majoritariamente
  **autodetect**. Schemas **explícitos** só onde o autodetect erraria a inferência int↔float entre
  arquivos: `team_season_averages` (shooting opponent), `player_props` (BetRivers), e
  `game_player_advanced_stats` (dezenas de campos forçados a FLOAT). As tabelas são **recriadas**
  com swap quase-atômico (cria temp → valida URI/schema → troca) p/ nunca deixar a tabela ausente.
- **Futebol** (`scripts/futebol/create_external_tables.py`): **13 tabelas** `raw_futebol_*` no
  dataset `futebol`, criadas com chamadas explícitas. As 4 simples (`leagues`, `teams`, `players`,
  `fixtures`) usam autodetect; as **9 restantes têm schema explícito** porque o autodetect coage
  strings numéricas a FLOAT e **perde os `"%"`** (ex.: posse de bola `"58%"`, odds/value decimais,
  rating). Esses campos ficam **STRING** e o cast vai para o staging dbt.

⚠️ **Custo:** external tables sobre NDJSON sofrem **full scan a cada query/build** (sem predicate
pushdown). A alavanca de custo é **frequência de build**, não filtro `WHERE`.

### 5.2 Cloud Run — serviços de extração

Cada endpoint é um serviço Cloud Run independente. Não há `Dockerfile` em `cloud_run/`: o build é
via **Cloud Buildpacks**, e o runtime HTTP é o `functions-framework`. O padrão de todo
`cloud_run/<svc>/main.py` é um wrapper `@functions_framework.http` que injeta `scripts/` no
`sys.path` e chama o `main()` do `scripts/extract_*.py` correspondente — toda a lógica fica em
`src/`. Retorna `{"status": "success"|"error"}` com HTTP 200/500.

São **29 serviços deployados**:

| Grupo | Qtde | Serviços |
|---|---|---|
| NBA extractors | 10 | `extract-active-players`, `-games`, `-game-player-stats`, `-game-player-stats-period`, `-game-player-advanced-stats`, `-season-averages`, `-team-season-averages`, `-player-injuries`, `-team-standings`, `-betting-odds` |
| NBA player props | 3 | `extract-player-props-draftkings` / `-caesars` / `-betrivers` (vendor **hardcoded no wrapper**) |
| Futebol extractors | 13 | `extract-leagues`, `-teams`, `-players`, `-fixtures`, `-fixture-statistics`, `-fixture-events`, `-fixture-lineups`, `-fixture-player-stats`, `-team-season-stats`, `-standings`, `-injuries`, `-odds`, `-predictions` |
| Compartilhados | 3 | `sync-bq-to-postgres`, `notify-execution`, `daily-summary` |

> `cloud_run/extract_player_props/` (sem vendor) é **órfão** — não está em `NBA_SERVICES` e não é
> deployado; foi substituído pelas 3 variantes por casa.

Parâmetros via request (poucos serviços lêem do `request`):
- `extract-fixtures`: `?mode=current|backfill` → injeta `FIXTURES_MODE` (padrão do toggle backfill).
- `extract-odds`: chama o extractor direto p/ devolver `saved_count` ao gate do workflow.
- `sync-bq-to-postgres`: `?sport=nba|futebol`, `?env=prd|dev` e `?tables=all|<csv>`.
- `daily-summary`: `?date=YYYY-MM-DD` (default: dia anterior em BRT).

### 5.3 Deploy

**`scripts/deploy_cloud_run.sh`** — deploya 1 serviço, todos de um esporte, ou todos. Defaults:
região `us-east1`, `512Mi`, `1 CPU`, timeout `3600s`, `--no-allow-unauthenticated`, SA de runtime
`ExtractScripts@<proj>` (override por `SERVICE_ACCOUNT` no `.env`). Empacotamento: cria um
`mktemp -d`, copia `main.py` + `requirements.txt` (+ `Procfile`) + `cp -r src/ scripts/` e roda
`gcloud run deploy --source <temp>` (é assim que `src/`+`scripts/` entram em cada serviço). Há
**4 ramos de config**:

| Serviço | Memória/timeout | Secrets injetados | Env |
|---|---|---|---|
| extractors (default) | 512Mi / 3600s | `BALLDONTLIE_KEY`, `API_FOOTBALL_KEY` | `GCS_BUCKET_NAME, GCP_PROJECT_ID, SEASON, LOG_LEVEL` |
| `sync-bq-to-postgres` | 1Gi / 900s, `max-instances 1` | `SUPABASE_PG_URL_PRD/DEV` | `GCP_PROJECT_ID, LOG_LEVEL` (+ runtime Python 3.13) |
| `daily-summary` | 600s | `GMAIL_USER/APP_PASSWORD`, `NOTIFY_EMAIL` | `GCP_PROJECT_ID, LOG_LEVEL` |
| `notify-execution` | — | `GMAIL_USER/APP_PASSWORD`, `NOTIFY_EMAIL` | — |

**`scripts/deploy_workflows.sh`** — deploya os 10 workflows com a SA explícita
`workflowsde@<proj>` (⚠️ sem isso o workflow novo cai na compute SA, sem `run.invoker`, e dá 403
silencioso → `PARTIAL_FAILURE`). Editar o YAML local **não** muda produção: é preciso rodar este
script.

**`analytics-engineering/build-and-push.sh`** (fora deste repo, mas no caminho crítico) — builda e
empurra a imagem Docker do dbt (`dbt_nba` ou `dbt_futebol`) para o Artifact Registry; é a imagem
que os **Cloud Run Jobs de dbt** executam. ⚠️ Mudar modelos dbt exige `build-and-push.sh` +
`gcloud run jobs update` — editar só o YAML do workflow falha silenciosamente.

### 5.4 Secret Manager

`scripts/setup_secrets.sh` é idempotente e cria **apenas** `BALLDONTLIE_KEY` e `API_FOOTBALL_KEY`,
concedendo `secretmanager.secretAccessor` à SA de runtime. Os demais segredos (`GMAIL_USER`,
`GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL`, `SUPABASE_PG_URL_PRD/DEV`) precisam existir **out-of-band**.
Os serviços os consomem via `--set-secrets NOME=SECRET:latest` (vira env var em runtime).

### 5.5 GCP Workflows — orquestração

10 workflows YAML na raiz (deployados por `deploy_workflows.sh`). Padrões comuns: chamam Cloud Run
via `http.get`/`http.post` com `auth.type: OIDC`; jobs dbt via conector
`googleapis.run.v2...jobs.run`; retry `max_retries: 3` (backoff 5→60). **SA de execução de todos:
`workflowsde@`**.

| Workflow | Tipo | Sequência (resumo) |
|---|---|---|
| `workflow-data-engineering` | NBA diário (full) | 13 extractors em **2 blocos paralelos** → job `dbt-nba` (build completo) → sync PRD+DEV |
| `workflow-bets` | NBA incremental | 4 extractors (props×3 + odds) ∥ → `dbt-nba --select stg_betting_odds+ stg_player_props+` → sync parcial |
| `workflow-injury-report` | NBA incremental | `extract-player-injuries` → `dbt-nba --select stg_player_injuries+ dim_teams+` → sync parcial |
| `workflow-futebol` | Futebol diário | **sequencial**: leagues→teams→players→**fixtures**→stats→events→lineups(real)→player-stats→standings→injuries → `dbt-futebol` (DAG completo) |
| `workflow-futebol-team-stats` | Futebol semanal | `extract-team-season-stats` → `dbt-futebol --select +fact_team_season_stats` |
| `workflow-futebol-odds` | Futebol poll ~15min | `extract-odds` → gate `saved_count>0` → `dbt-futebol` enxuto (recalcula o Motor de Score) |
| `workflow-futebol-predictions` | Futebol poll ~15min | `extract-predictions` → gate → `dbt-futebol --select stg/fact predictions` |
| `workflow-futebol-lineups` | Futebol poll ~15min | `extract-fixture-lineups mode=pregame` → gate → `dbt` lineups |
| `workflow-futebol-injuries` | Futebol poll ~horário | `extract-injuries mode=pregame` → gate → `dbt` desfalques (**writer disjunto** do Motor) |
| `workflow-daily-summary` | Observabilidade | chama `daily-summary` (1 email/dia) |

Detalhes que importam:
- **Gates.** Os polls de futebol só rodam o dbt se `saved_count>0` (`skip_dbt`). O sync NBA PRD é
  gated em `dbt_ok` (se o dbt falhou, PRD é pulado; DEV sempre roda).
- **Recovery dbt.** Os workflows de futebol re-tentam o job com `--full-refresh` no `except`
  (corrige drift table↔view). ⚠️ Esse drift (modelo vira `view` no código mas é `table` no BQ, ou
  vice-versa) já causou `PARTIAL_FAILURE` silencioso.
- ⚠️ **PARTIAL_FAILURE silencioso.** Nenhum workflow falha "hard": todo `except` marca
  `workflow_status=PARTIAL_FAILURE`, agrega `failed_services` e segue. A **execução termina
  SUCCEEDED no GCP mesmo com falhas.** Por isso o canal real de detecção é o `daily-summary` + os
  `log_completion` com `severity WARNING` — não o status do Workflow.

### 5.6 Cloud Scheduler — gatilhos

⚠️ **Os jobs do Cloud Scheduler NÃO estão versionados no repositório** (não há `.tf` nem
`gcloud scheduler jobs create` em scripts; o exemplo em `README.md:728` é genérico e
desatualizado — usa `us-central1`, diverge do `us-east1` real). Cron/timezone exatos vivem só no
Console GCP. O que está documentado nos headers dos YAML + docs + memória do projeto:

| Workflow disparado | Frequência (documentada) | SA do job |
|---|---|---|
| `workflow-daily-summary` | ~00:05 BRT diário (job `daily-summary`) | `schedulerde@` |
| `workflow-futebol-odds` | ~15 min (job `futebol-odds-pregame`) | `schedulerde@` |
| `workflow-futebol-predictions` | ~15 min (job `futebol-predictions-pregame`) | `schedulerde@` |
| `workflow-futebol-lineups` | ~15 min | `schedulerde@` |
| `workflow-futebol-injuries` | ~horário (custo-ótimo) | `schedulerde@` |
| `workflow-futebol` | diário (`mode=current`) | `schedulerde@` |
| `workflow-futebol-team-stats` | 1×/semana | `schedulerde@` |
| `workflow-data-engineering` / `-bets` / `-injury-report` | **crons NBA pausados** (sync recorrente concentrado no `workflow-data-engineering`) | — |

### 5.7 IAM — service accounts

A cadeia de identidade tem 3 SAs principais (o erro mais comum é trocar uma pela outra):

| SA | Papel | Usada por |
|---|---|---|
| `schedulerde@` | `workflows.invoker` | Cloud Scheduler → dispara o Workflow. ⚠️ usar `ExtractScripts@` aqui dá 403 silencioso |
| `workflowsde@` | `run.invoker` / `run.admin` | Workflow → invoca Cloud Run/Jobs. ⚠️ workflow novo cai na compute SA → 403 → `PARTIAL_FAILURE` |
| `ExtractScripts@` | `storage.objectAdmin` | Runtime dos serviços Cloud Run → escreve no GCS |

Durante o build, as SAs de Cloud Build (`<projnum>@cloudbuild`, `<projnum>-compute@developer`)
precisam de `storage.admin`/`logging.logWriter`/`artifactregistry.writer` (ver `README.md`).

### 5.8 Sync BigQuery → Supabase Postgres

`src/sync/bq_to_postgres.py` (+ serviço `sync-bq-to-postgres`) materializa as marts no Postgres que o
app consome. **Sport-aware** (`?sport=nba|futebol`, default `nba`): `get_sync_target(sport)` resolve o
trio (dataset BQ, schema Postgres, allowlist ordenada). Desde 2026-06 cobre **NBA e futebol** — o
futebol saiu do FDW BigQuery (`wrappers`/`bq_futebol`/`futebol.sync_all`/pg_cron) pro mesmo sync.

| sport | dataset BQ | schema PG | allowlist | agendamento |
|---|---|---|---|---|
| `nba` | `nba` | `nba_mart` | `MART_TABLES_ORDERED` (15) | `workflow-data-engineering` (fase 3) |
| `futebol` | `futebol` | `futebol` | `FUTEBOL_SYNC_TABLES_ORDERED` (21) | `workflow-futebol-sync` + scheduler horário |

- **Leitura sem custo de scan:** `bq.list_rows()` (grátis), não `query()`. ⚠️ `list_rows` **não lê
  view** — todo modelo sincronizado precisa ser `table` no BQ (no futebol, 5 ex-views viraram table).
- **Escrita:** por tabela, **TRUNCATE + COPY tipado** (psycopg3) numa única transação. COPY tipado
  preserva `None`→NULL vs `''`→string vazia.
- **Colunas complexas:** `_is_complex_field` pula campos BQ REPEATED/RECORD (o Postgres nativo é
  escalar) — ex.: futebol `dim_leagues.coverage`, `evidencias`/`avisos`; as RPCs reconstroem.
- **Tabelas:** allowlist por esporte (`config.py`), na ordem **dim → fact → derivada**.
- **skip-if-unchanged:** `<schema>._sync_state` guarda o `bq_modified` da última sync; pula a tabela
  se nada mudou (`force=true` ignora).
- **Parity check pré-flight:** compara colunas/tipos BQ↔PG **antes** de qualquer TRUNCATE; se houver
  drift, aborta com `aborted_schema_drift` (HTTP 500) sem truncar nada.
- **PRD e DEV** são bancos Supabase independentes, cada um com seu `_sync_state`; o workflow chama
  `?env=prd` e depois `?env=dev`. Conexões usam **porta 5432** (sessão), não 6543 (pgbouncer não
  suporta COPY).
- **IAM:** a SA runtime `extractscripts@` precisa de leitura no dataset BQ de cada esporte (READER no
  dataset `futebol` adicionado). O schema `futebol` é RPC-only (igual `nba_mart`); o app lê via RPCs
  `public.get_futebol_*`. DDL não-migration em `prop-play-predictor/docs/futebol-prod-deploy.sql`.

### 5.9 Observabilidade — notificação e resumo diário

Ambos enviam por **Gmail SMTP_SSL** (`smtp.gmail.com:465`) com os secrets `GMAIL_*`/`NOTIFY_EMAIL`.

- **`daily-summary`** (`src/reporting/daily_summary.py`) — modelo atual, **1 email/dia**. Lê as
  execuções do dia anterior no **Cloud Logging** (passo `log_completion` de cada workflow) e cruza
  com a **Workflow Executions API** p/ capturar `FAILED`/`CANCELLED` que nunca logam; monta uma
  tabela HTML (runs/OK/parcial/falha/duração). Requer `logging.viewer` + `workflows.viewer`.
- **`notify-execution`** — modelo antigo, 1 email **por execução** (texto plano). Workflow-agnóstico;
  ainda chamado por alguns workflows.

### 5.10 Testes dbt em produção — onde olhar o resultado

Os testes do `dbt_futebol` rodam em **duas fases** do `workflow_futebol.yml`, ambas **depois** do
mart e **sem gate**. A separação é deliberada: `dbt build` faria teste vermelho a montante **pular**
os modelos a jusante, o mart não reconstruiria e o board congelaria. Guarda de qualidade não derruba
o produto.

| Fase | Seleção | Testes | Severidade típica | Sinal |
|---|---|---|---|---|
| 4 | `dbt test --select tag:guarda` | 36 | `error` | `guardas_status` no `log_completion` |
| 5 | `dbt test --exclude tag:guarda tag:taskf` | 305 (291 data + 14 unit) | `warn` | linha `Done. PASS=…` do log do job |

União das duas = a suíte inteira, sem pagar duas vezes pelo scan. `tag:taskf` fica de fora: os 5
testes da task [F] leem o dataset de **medição** `futebol_taskF`, que produção não constrói.

**Onde o time olha:** o **e-mail do resumo diário** (`daily-summary`, ~00:05 BRT), seções
"Guardas de qualidade de dado" e "Suite dbt". Nenhuma das duas fases derruba workflow, então o
e-mail é o único canal — e o assunto carrega os tokens `[GUARDA]` e `[SUITE]`.

**Por que a fase 5 se lê pelo LOG e não pelo status.** `dbt test` sai com **0 quando tudo que falhou
é `warn`** — e hoje tudo que a fase 5 acusa é warn. Um status verde/vermelho ficaria verde para
sempre. Por isso o workflow emite `suite_execution` (nome da execução do Cloud Run Job) junto de
`suite_status`, e o resumo diário lê a linha de fechamento do dbt nessa execução:

```
Done. PASS=295 WARN=10 ERROR=0 SKIP=0 NO-OP=0 TOTAL=305
```

**`WARN` é o estado normal — o valor está em comparar com ontem.** Baseline medido em 2026-08-21
(execução `dbt-futebol-tqjk8`), 10 WARN: 3 `relationships` de órfãos conhecidos
(`fact_injuries_snapshot`→`dim_players` 4.742, `fact_fixture_lineups_players`→`dim_players` 6.214,
`fact_standings_snapshot`→`dim_teams` 8), `fact_injuries_snapshot`→`fact_fixtures` (90),
`int_futebol_desfalques`→`dim_players` (33), `assert_per_fixture_coverage` (29, baseline
estrutural), `accepted_values` de posição em `dim_players` (1) e 3 de grão/nulo em staging de
lineups/player_stats. **WARN subindo é regressão nova, mesmo com `ERROR=0`.** Só `ERROR≥1` acende o
assunto — um token que pisca todo dia treina o time a ignorar o e-mail.

**Custo.** Medido na mesma execução: **7,66 GB faturados** (6,10 GB processados) em 319 jobs de BQ,
~2min12s de parede — ~US$ 0,044/dia a on-demand. As staging são **views sobre NDJSON externo**,
então todo teste sobre elas é full scan: **70% do custo (5,03 GiB) vem de 61 testes de
`stg_futebol_*`**; os 258 restantes, sobre marts já materializados, custam 2,11 GiB. Se a conta
apertar, a alavanca é acrescentar `stg_futebol_*` ao `--exclude` (−70% do custo, mantendo 258 dos
305 testes) — **não** desligar a fase. ⚠️ O job tem `maxRetries=1`: um dia com `ERROR≥1` roda a
suíte **duas vezes** antes de a execução falhar.

**O que as fases NÃO pegam:** imagem dbt velha. As duas rodam da mesma imagem pré-buildada, então
código mergeado e não buildado passa verde nas duas. Esse é o papel da seção "Procedência" do mesmo
e-mail (§5.9) e do detector horário no `analytics-engineering`.

---

## 6. Operação

### 6.1 Variáveis de ambiente (`.env`)

| Var | Uso |
|---|---|
| `BALLDONTLIE_KEY` | auth NBA |
| `API_FOOTBALL_KEY` | auth futebol |
| `GCS_BUCKET_NAME` | landing (`smartbetting-landingzone`) |
| `GCP_PROJECT_ID` | `smartbetting-dados` (GCS/BQ) |
| `SEASON` | temporada NBA corrente (2025) |
| `LOG_LEVEL` | `INFO` |
| `SUPABASE_PG_URL_PRD` / `_DEV` | sync (porta 5432) |
| `BACKFILL_SEASONS` | (opcional) truthy → NBA itera todas as `SEASONS` |

### 6.2 Comandos comuns

```bash
# Auth GCP (ADC)
gcloud auth application-default login

# Rodar um extrator local (lê SEASON/mode do .env). Usar python3 no macOS.
python3 scripts/extract_games.py
python3 scripts/futebol/extract_fixtures.py        # mode via FIXTURES_MODE

# External tables
python3 scripts/create_bigquery_external_tables.py # NBA
python3 scripts/futebol/create_external_tables.py  # Futebol

# Deploy
./scripts/deploy_cloud_run.sh                       # tudo
./scripts/deploy_cloud_run.sh futebol extract-odds  # um serviço
./scripts/deploy_workflows.sh workflow-futebol-odds # um workflow
./scripts/setup_secrets.sh                          # secrets de API
```

### 6.3 Backfill vs current

- **NBA:** `BACKFILL_SEASONS=1` faz os fatos por-data iterarem todas as `SEASONS` (`[2023,2024,2025]`).
- **Futebol:** disparo manual do workflow com `mode=backfill` (ou `*_MODE=backfill` local). O
  backfill captura anos anteriores (Brasileirão 2024/2025); o `current` é o diário (ano corrente +
  Copa). ⚠️ Forward-only (odds/predictions/lineups confirmados) **não tem backfill** — janela
  passada não se reconstrói.

---

## 7. Como estender

**Novo endpoint** (segue o template do `.cursorrules`):
1. Config em `src/config.py` (`ENDPOINT_CONFIGS` p/ NBA, ou tuplas/janelas p/ futebol).
2. Método no client (`BallDontLieClient` ou `ApiFootballClient`).
3. Extractor em `src/extractors/` herdando de `BaseExtractor` (ou `PerFixtureExtractor`).
   ⚠️ Se devolver `{key:[...]}`, registrar `key` em `array_keys` no `gcs_storage.py`.
4. Script fino em `scripts/` (sem `argparse`).
5. Serviço em `cloud_run/<svc>/` (wrapper `functions_framework` + `requirements.txt`).
6. External table (`create_*_external_tables.py`) + registrar no `workflow_*.yml` + redeploy.

**Nova liga de futebol** (ver [`EXPANSAO_CAMPEONATOS_APIFOOTBALL.md`](EXPANSAO_CAMPEONATOS_APIFOOTBALL.md)):
adicionar o `league_id` nas tuplas de config (`LEAGUES_*`, `FIXTURES_*`, etc.; `INJURIES_*`/odds/
predictions **só** se `coverage=TRUE`) + 2 `CASE` no dbt. **Zero extrator novo.**

---

## 8. Armadilhas conhecidas (checklist de operação)

- **Bucket:** o real é `smartbetting-landingzone` (`.env`); o default `smartbetting-landing` do
  `config.py` é bucket morto (403 billing) — engana no debug.
- **`array_keys`:** extrator novo com `{key:[...]}` não registrado → 1 linha gigante no BQ.
- **Schema explícito:** arrays `{type,value}`/percentuais de futebol → autodetect vira FLOAT e nula
  os `"%"`. Usar STRING na external table e castar no dbt.
- **Rebuild de imagem dbt:** mudar modelos `dbt_*` exige `build-and-push.sh` + `jobs update`.
- **Redeploy de workflow:** editar `workflow_*.yml` local não muda produção; rodar
  `deploy_workflows.sh`.
- **SA certa:** Scheduler = `schedulerde@`; Workflow = `workflowsde@`. Trocar → 403 silencioso.
- **PARTIAL_FAILURE silencioso:** Workflow termina SUCCEEDED mesmo com falha; confiar no
  `daily-summary`/logs `WARNING`.
- **drift table↔view (dbt):** fixar `materialized` no código; o recovery `--full-refresh` é rede de
  segurança, não cura.
- **Quota API-Football:** estouro = HTTP 200 + `errors`, não 429; o cliente levanta
  `ApiQuotaExceededError` para falhar o run (crítico nos polls forward-only).
- **Forward-only:** cada poll perdido (odds T-15m, predictions) = linha de fechamento/CLV perdida
  para sempre.
- **macOS do usuário:** usar `python3` (não `python`). dbt roda pelo `.venv` do
  `analytics-engineering`.

---

## 9. Referências

- [`PIPELINE_APIFOOTBALL.md`](PIPELINE_APIFOOTBALL.md) — vertical futebol (15 tabelas, schemas, status).
- [`EXPANSAO_CAMPEONATOS_APIFOOTBALL.md`](EXPANSAO_CAMPEONATOS_APIFOOTBALL.md) — adicionar ligas.
- [`../README.md`](../README.md) — guia operacional NBA (parcialmente desatualizado nos exemplos de CLI).
- [`../CLAUDE.md`](../CLAUDE.md) / [`../.cursorrules`](../.cursorrules) — regras de modularização.
- `analytics-engineering/` — camada dbt (downstream) e `build-and-push.sh`.
