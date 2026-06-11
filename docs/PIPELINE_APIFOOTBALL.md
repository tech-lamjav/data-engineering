# Pipeline de ingestão API-Football

Documento de contexto e plano para a nova vertical de dados de **futebol** (API-Football v3) dentro do `data-engineering/`. Paralelo ao pipeline NBA existente (balldontlie.io), reutilizando a infraestrutura GCS → BigQuery → dbt.

> **Status**: planejamento. Endpoints específicos e schemas das 15 tabelas serão detalhados em subtasks futuras. Implementação aguarda definição.

---

## 1. Contexto e objetivo

Pipeline de coleta de dados da API-Football para alimentar **dois produtos paralelos**:

- **Brasileirão Série A** — temporadas 2024, 2025 e 2026 (produto sério de *value betting*)
- **Copa do Mundo 2026** — temporada 2026 completa (complemento)

Todas as tabelas de **fato** terão uma coluna `competition` (`brasileirao` | `copa_mundo`) para separar contexto.

---

## 2. API e credenciais

| Item | Valor |
|---|---|
| Base URL | `https://v3.football.api-sports.io` |
| Header de auth | `x-apisports-key: <API_FOOTBALL_KEY>` |
| Env var | `API_FOOTBALL_KEY` (já presente em `.env`) |
| Envelope de resposta | `{get, parameters, errors, results, paging:{current,total}, response:[...]}` |
| Paginação | Page-based, **100 itens/página**, total em `paging.total` |
| Plano | **Pro** — US$ 20/mês — **7.500 requests/dia** |
| Conta | Victor Diody — `tecnologia@smartbetting.app` |
| Subscription | Ativa até **2026-06-11** ⚠️ |

> ⚠️ **Atenção à renovação**: a subscription vence em 2026-06-11, **mesma janela em que a Copa de 2026 estreia**. Garantir renovação antes para evitar falha de ingestão no meio do torneio.

Validação feita em 2026-05-25 contra `GET /status` — auth OK, 0 requests usados, plano Pro confirmado.

---

## 3. Escopo

### 3.1 Competições

| Liga | IDs (a validar) | Temporadas |
|---|---|---|
| Brasileirão Série A | `71` (esperado) | 2024, 2025, 2026 |
| Copa do Mundo | `1` (esperado) | 2026 |

> IDs precisam ser confirmados via `GET /leagues?search=...` antes da Fase 1.

### 3.2 Endpoints a consumir

> **Aguardando definição do usuário.** Lista será preenchida em subtask própria. Conjunto candidato (baseado nos endpoints da API-Football v3):
> - Dimensões: `/leagues`, `/teams`, `/players`, `/players/squads`
> - Fixtures: `/fixtures`, `/fixtures/statistics`, `/fixtures/events`, `/fixtures/lineups`, `/fixtures/players`, `/fixtures/headtohead`
> - Snapshots: `/standings`, `/injuries`
> - Stats de temporada: `/teams/statistics`, `/players` (com filtro season)
> - Odds + predictions: `/odds`, `/predictions`

### 3.3 Tabelas BigQuery (15 no total)

Dataset: **`apifootball_raw`** (a criar em `smartbetting-dados`, `us-east1`).

Cada tabela tem subtask própria com schema completo (colunas, tipos, descrição, particionamento). **Subtasks ainda não recebidas** — preencher abaixo conforme chegarem:

| # | Tabela | Subtask | Status |
|---|---|---|---|
| 1 | `dim_leagues` | confirmar coverage Brasileirão 2024/25/26 + Copa 2026 | pendente |
| 2 | `dim_teams` | — | pendente |
| 3 | `dim_players` | catálogo via `/players?league=&season=` (paginado) → external `raw_futebol_players` → dbt dedup por player_id | implementado |
| 4 | `fact_fixtures` | tabela mãe via `/fixtures?league=&season=` (paginado) → external `raw_futebol_fixtures` → dbt `fact_fixtures` (table, particionada por `DATE(date_utc)`, cluster `competition,season,home_team_id`, dedup por `fixture_id`) | implementado |
| 5 | `fact_fixture_stats` (statistics) | per-fixture via `/fixtures/statistics?fixture=` (1 chamada/jogo FT, janela 3d + skip-if-exists) → external `raw_futebol_fixture_statistics` (2 linhas/jogo, `statistics` aninhado) → dbt `stg_futebol_fixture_statistics` (pivot) → `fact_fixture_stats` (table, part. `DATE(date_utc)`, cluster `fixture_id,team_id`, `team_side` via join `fact_fixtures`, `expected_goals`+`goals_prevented` da própria API) | implementado |
| 6 | `fact_fixture_events` | per-fixture via `/fixtures/events?fixture=` (1 chamada/jogo FT, janela 3d + skip-if-exists) → external `raw_futebol_fixture_events` (N linhas/jogo, 1/evento; `event_order` carimbado no extractor p/ preservar ordem; team/player/assist aninhados) → dbt `stg_futebol_fixture_events` (flatten) → `fact_fixture_events` (table, part. `DATE(date_utc)`, cluster `fixture_id,event_type`, `team_side` via join `fact_fixtures`, dedup `fixture_id,event_order`) | implementado |
| 7 | `fact_fixture_lineups` + `fact_fixture_lineups_players` | per-fixture via `/fixtures/lineups?fixture=` em 2 fases — pós-jogo `_real` (fase 1g do `workflow_futebol.yml`, mode current/backfill) + pré-jogo `_confirmed` ~T-30min (`workflow_futebol_lineups.yml`, poll Scheduler a cada 15min, status NS+kickoff<45min, dbt condicional gated em `saved_count`) → external `raw_futebol_fixture_lineups` (2 linhas/jogo/fase, `startXI`/`substitutes` aninhados, schema explícito; key `fixture_lineups` em `array_keys`) → dbt `stg_*` (UNNEST WITH OFFSET) → `fact_fixture_lineups` (2 linhas/jogo: formação+técnico) e `fact_fixture_lineups_players` (~46/jogo: titulares+banco, `is_starter`), latest-wins (`real` vence `confirmed` por `loaded_at`), part. `DATE(date_utc)`, cluster `fixture_id,team_id`, join `fact_fixtures` p/ competition/season/`team_side`. Backfill 2024/25 completo (760 jogos, 1520+34686 linhas). | implementado |
| 8 | `fact_fixture_player_stats` | per-fixture via `/fixtures/players?fixture=` (1 chamada/jogo FT, janela 3d + skip-if-exists) → external `raw_futebol_fixture_player_stats` (N linhas/jogo, 1/jogador; `statistics` é struct único aninhado — flatten, **não** é `{type,value}` como o statistics; schema explícito; key `fixture_player_stats` em `array_keys`; ⚠️ `penalty.commited` com grafia errada da API, `fouls.committed` certo) → dbt `stg_futebol_fixture_player_stats` (flatten direto sem pivot; `rating`/`passes_accuracy` via SAFE_CAST) → `fact_fixture_player_stats` (table, part. `DATE(date_utc)`, cluster `fixture_id,team_id,player_id`, `team_side` via join `fact_fixtures`, dedup `fixture_id,player_id`, self-contained). `dim_players` (subtask 3) refeito como UNION catálogo + `fixture_only` (quem jogou mas falta no catálogo entra com id/nome/foto/posição; coluna `source`) → garante FK fact→dim; teste singular diagnostica gaps do catálogo. Service `extract-fixture-player-stats` deployado, imagem `dbt_futebol` rebuildada + job atualizado, `workflow_futebol.yml` redeployado (rev 000008, SA `workflowsde@`). Backfill 2024/25 completo (760 fixtures FT → 759 com dados, 1 sem player stats na API; `fact_fixture_player_stats` = 34.6k linhas / 759 jogos, 23.9k com minutos). `dim_players` union: 1696 catalog + 3 fixture_only. dbt run/test 100% verdes (15 testes). | implementado |
| 9 | `fact_team_season_stats` | per-time via `/teams/statistics?league=&season=&team=` (1 chamada/time, lista de times lida de `raw_futebol_teams_{mode}.json` via `get_team_ids_from_storage`) → external `raw_futebol_team_season_stats` (1 linha/time×liga×season, 1 arquivo/mode latest-only; objeto único da API curado em forma aninhada — buckets por minuto/under_over/cards/lineups descartados; schema explícito pq averages/percentage vêm STRING; key `team_season_stats` em `array_keys`) → dbt `stg_futebol_team_season_stats` (flatten + SAFE_CAST das médias/percentuais, `for` escapado) → `fact_team_season_stats` (table, part. `snapshot_date`, cluster `team_id,season`, **self-contained**: competition de `requested_league_id`, sem join em fact_fixtures; dedup `team_id,liga,season`). Schedule SEMANAL via `workflow_futebol_team_stats.yml` (separado do diário, Scheduler 1x/semana, SA `schedulerde@`). Input direto pro Poisson (força ataque/defesa, médias casa/fora). | implementado |
| 10 | `fact_standings_snapshot` | snapshot diário da tabela do campeonato via `/standings?league=&season=` (1 chamada/liga×season; fase 1i do `workflow_futebol.yml` diário, mode current/backfill) → external `raw_futebol_standings` (1 linha/time×grupo — a Copa traz 12 grupos + "Ranking of third-placed teams", mesmo time 2× → `group` integra a chave; ⚠️ diferente dos demais, arquivo **date-stampado** `raw_futebol_standings_{mode}_{YYYY-MM-DD}.json` — GCS acumula 1 snapshot/dia (histórico de evolução) e re-run no dia sobrescreve = idempotente; suporte a `date` adicionado no branch futebol do `get_gcs_path`, non-breaking; schema explícito — `all`/`group`/`goals.for` são keywords, crases no stg) → dbt `stg_futebol_standings` (flatten + renames status→rank_status, description→rank_description, update→standings_updated_at) → `fact_standings_snapshot` (table, part. `snapshot_date`, cluster `league_id,team_id`, **self-contained**, dedup `league_id,season,snapshot_date,group_name,team_id` latest-wins). `snapshot_date` = data da coleta; a API não tem histórico diário → backfill = tabela FINAL Brasileirão 2024/2025 (40 linhas; `standings_updated_at` marca o fim real da temporada) + current 2026 (20 Brasileirão + 60 Copa). Service `extract-standings` deployado, imagem `dbt_futebol` rebuildada + job atualizado, workflow redeployado. dbt run/test 100% verdes (16 testes). | implementado |
| 11 | `fact_h2h` (head-to-head) | — | pendente |
| 12 | `fact_injuries` | — | pendente |
| 13 | `fact_odds` | — | pendente |
| 14 | `fact_predictions_api` | — | pendente |
| 15 | (15ª tabela — a definir) | — | pendente |

---

## 4. Plano de execução (6 fases)

### Fase 1 — Setup e validação (1-2 dias)
- Criar dataset BigQuery `apifootball_raw`
- Subtask 1 (`dim_leagues`) — confirmar coverage de Brasileirão 2024/2025/2026 e Copa 2026

### Fase 2 — Dimensões (1 dia)
- Subtasks 2 e 3 — `dim_teams` e `dim_players`

### Fase 3 — Carga histórica Brasileirão 2025 + 2026 (3-4 dias)
- Subtasks 4-9 — `fact_fixtures`, stats, events, lineups, player_stats, team_season_stats
- ~400 jogos × endpoints de jogo finalizado

### Fase 4 — Snapshots e odds (2 dias)
- Subtasks 10-13 — standings, h2h, injuries, odds

### Fase 5 — Predictions e expansão (1 dia)
- Subtask 14 — `predictions_api`
- Expandir Brasileirão 2024 + Copa 2026 com mesmo schema

### Fase 6 — Pipeline incremental (2 dias)
- Subtask 15 — schedule diário automatizado

---

## 5. Volume estimado

| Item | Volume |
|---|---|
| Carga histórica completa | ~12.000-13.000 chamadas (~2 dias rodando em batch) |
| Manutenção diária | ~50-200 chamadas/dia |
| Limite do plano Pro | 7.500 chamadas/dia |

Folga confortável — backfill cabe em 2 dias de execução, operação diária consome <3% da quota.

---

## 6. Arquitetura no repo

Espelha o padrão NBA (balldontlie) já existente em `src/`, `scripts/`, `cloud_run/`, GCP Workflows.

### 6.1 Refatorações necessárias na base

A base atual está acoplada a balldontlie + NBA. Antes de criar os extractors de futebol:

| Arquivo | Mudança |
|---|---|
| `src/clients/base_client.py` | Aceitar `auth_headers: dict` em vez de só `api_key` (API-Football usa `x-apisports-key`, não `Authorization: Bearer`) |
| `src/extractors/base_extractor.py` | Desacoplar do `BallDontLieClient` — receber client via injeção ou parametrizar |
| `src/config.py::get_gcs_path()` | Generalizar prefixo `nba/` → `{sport}/` e nome do arquivo `raw_nba_*` → `raw_{sport}_*` |
| `src/storage/gcs_storage.py` | Aceitar `sport` no `upload_json()` (default `nba` p/ retrocompatibilidade) |

> Importante: refatoração deve ser **não-breaking** para o pipeline NBA. Manter defaults atuais.

### 6.2 Layout proposto

```
data-engineering/
├── src/
│   ├── clients/
│   │   └── api_football_client.py          # NOVO — herda de BaseClient
│   ├── extractors/
│   │   └── futebol/                        # NOVO — subdir
│   │       ├── __init__.py
│   │       ├── leagues_extractor.py
│   │       ├── teams_extractor.py
│   │       ├── fixtures_extractor.py
│   │       └── ...                         # 1 por endpoint
│   └── config.py                           # ADICIONAR vars
├── scripts/
│   ├── extract_futebol_leagues.py          # NOVO — thin orchestrators
│   ├── extract_futebol_fixtures.py
│   └── ...
├── cloud_run/
│   ├── extract_futebol_leagues/            # NOVO — wrappers HTTP
│   ├── extract_futebol_fixtures/
│   └── ...
├── workflow_futebol.yml                    # NOVO — separado dos NBA
└── docs/
    └── PIPELINE_APIFOOTBALL.md             # este arquivo
```

### 6.3 Config a adicionar em `src/config.py`

```python
# API-Football Configuration
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

# Ligas (IDs a confirmar via /leagues)
FUTEBOL_LEAGUES = {
    "brasileirao": 71,    # confirmar
    "copa_mundo": 1,      # confirmar
}

FUTEBOL_SEASONS = {
    "brasileirao": [2024, 2025, 2026],
    "copa_mundo": [2026],
}
```

### 6.4 Destinos

| Camada | Destino |
|---|---|
| GCS | `gs://smartbetting-landing/futebol/{endpoint}/...` |
| BigQuery | `smartbetting-dados.apifootball_raw.*` (dataset novo, location `us-east1`) |
| dbt | `analytics-engineering/dbt_futebol/` (project novo, etapa posterior) |
| Workflow | `workflow_futebol.yml` (separado dos NBA) |

---

## 7. Regras herdadas do `.cursorrules`

Valem **integralmente** para o pipeline de futebol:

- Lógica de negócio em `src/`, scripts thin que importam de `src/`
- **Sem `argparse`** — toda config via `src/config.py` + env vars
- Cloud Run = wrapper `functions_framework` chamando `main()` do script
- Mesmo código roda local e em Cloud Run

---

## 8. Definição de pronto

- [ ] 15 tabelas criadas em `apifootball_raw` com schema documentado
- [ ] Brasileirão 2025 + 2026 com dados completos validados
- [ ] Copa 2026 com dados disponíveis até a data
- [ ] Pipeline incremental rodando em schedule
- [ ] README do pipeline com setup e troubleshooting (atualizar este doc)
- [ ] Subscription API-Football renovada (vence 2026-06-11)

---

## 9. Pontos em aberto

1. **Endpoints específicos** — usuário ainda vai passar a lista priorizada
2. **Schemas das 15 tabelas** — virão via subtasks
3. **Validar IDs reais** das ligas via `/leagues?search=...` na Fase 1
4. **15ª tabela** — escopo ainda não nomeado no plano
5. **dbt project** — `dbt_futebol/` ficará para depois das tabelas raw estarem populadas
6. **Renovação da chave** — confirmar com Victor Diody antes de 2026-06-11
7. **Sync BQ → Supabase Postgres (futebol) — postergado** — implementar quando houver consumer (UI futebol no `prop-play-predictor` ou outro produto). Caminho previsto: estender `cloud_run/sync_bq_to_postgres/` com query param `?sport=futebol`; adicionar em `src/config.py` as constantes `MART_PG_SCHEMA_FUTEBOL` e `MART_TABLES_ORDERED_FUTEBOL`; criar schema `futebol_mart` no Supabase (PRD+DEV); adicionar fase de sync em `workflow_futebol.yml` espelhando fase 3 do `workflow_injury_report.yml`.
8. **Notificação por email** — **já coberta** ✓ pelo Cloud Run `notify-execution` existente (workflow-agnóstico). `workflow_futebol.yml` já chama no passo `send_notification`.

---

## 10. Decisões já tomadas (referência rápida)

| Decisão | Valor |
|---|---|
| Esporte | Futebol (soccer), vertical nova paralela à NBA |
| Prefixo GCS | `futebol/` |
| Dataset BQ | `apifootball_raw` |
| Coluna de separação | `competition` (`brasileirao` \| `copa_mundo`) em todas as fatos |
| Provider | API-Football v3 direto (não via RapidAPI) |
| Plano | Pro — 7.500 req/dia |
| Ordem de implementação | Refatorar base → client → extractors por endpoint conforme subtasks chegam |
