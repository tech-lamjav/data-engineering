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

Dataset: **`futebol`** (em `smartbetting-dados`, `us-east1`). *(O plano original chamava de `apifootball_raw`, mas a implementação padronizou `futebol` — vide `analytics-engineering/profiles.yml` e `sources.yml`.)*

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
| 11 | `fact_h2h` (head-to-head) | **view** sobre `fact_fixtures` — **zero chamada API** (`/fixtures/headtohead` é redundante: os jogos já estão na tabela mãe; sem extractor/external table/serviço novo). dbt `fact_h2h` (materialized `view`; `h2h_pair_key` = CONCAT(LEAST, '-', GREATEST) dos team_ids → `WHERE h2h_pair_key='X-Y'` traz o confronto independente do mando; só finalizados **FT/AET/PEN** — padrão do projeto, inclui mata-mata da Copa por prorrogação/pênaltis, diverge do `='FT'` da spec; demais colunas espelham `fact_fixtures` 1:1 via `SELECT *` — view recriada no run diário logo após `fact_fixtures` acompanha evolução de schema; sem `dbt_loaded_at` próprio, em view viraria hora da query). `workflow_futebol.yml` com `+fact_h2h` no `--select` do job dbt + redeployado; imagem `dbt_futebol` rebuildada + job atualizado. Validação: 939 linhas = 939 fixtures finalizados, 314 pares distintos, 0 status fora do conjunto; últimos 5 Palmeiras×Flamengo (`'121-127'`, IDs de `dim_teams`) OK nas 3 temporadas; dbt run/test 100% verdes (86 testes). | implementado |
| 12 | `fact_injuries_snapshot` | snapshot diário de lesionados/suspensos via `/injuries?league=&season=` (1 chamada/liga×season, **não paginada** — como `/fixtures`, rejeita `page` e devolve o log de lesões da **temporada inteira**, ~2k linhas/liga, não os ~10-30 "atuais" que a spec supunha; fase 1j do `workflow_futebol.yml` diário, mode current/backfill) → external `raw_futebol_injuries` (date-stampado `raw_futebol_injuries_{mode}_{YYYY-MM-DD}.json` como standings; GCS acumula 1 snapshot/dia, re-run no dia sobrescreve = idempotente; schema explícito — ⚠️ `type`/`reason` vêm **aninhados em `player`** (não no topo), `fixture.date` ISO→TIMESTAMP) → dbt `stg_futebol_injuries` (flatten; `player.type`→injury_type, `player.reason`→injury_reason; sem teste de unicidade no stg pq a API repete linhas exatas) → `fact_injuries_snapshot` (table, part. `snapshot_date`, cluster `team_id,fixture_id`, **self-contained**, dedup `league_id,season,snapshot_date,fixture_id,player_id,injury_type,injury_reason` latest-wins — ⚠️ a API repete **linhas EXATAS**, daí o dedup). **Coverage gate**: validado `coverage.injuries` em `dim_leagues` — TRUE só p/ Brasileirão (71) 2024/25/26; **Copa do Mundo (1) 2026 = FALSE → EXCLUÍDA** (a API não fornece lesões da Copa). Service `extract-injuries` deployado, imagem `dbt_futebol` rebuildada + job atualizado, `workflow_futebol.yml` fase 1j + `+fact_injuries_snapshot` no `--select` + redeployado. Backfill 2024/25 + current 2026 = 6898 linhas (1668+3621+1609; 542 dups exatas colapsadas no 2026). dbt run/test verdes (17 pass, 1 warn = FK player_id→dim_players: 30 jogadores lesionados fora do catálogo, severity warn como subtask 8). | implementado |
| 13 | `fact_odds_snapshot` | snapshot pré-jogo de odds (coração do value betting) via `/odds?fixture=` em 3 janelas por jogo — **T-24h** (abertura), **T-1h** (intermediária) e **T-15min** (fechamento — habilita CLV real) — p/ CLV/EV/movimento de linha. **FORWARD-ONLY** (não dá pra reconstruir janelas de jogos passados, sem backfill). Poll ~15min via `workflow_futebol_odds.yml` (Scheduler `futebol-odds-pregame`, SA `schedulerde@`), espelhando o poll de escalações: **uma passada** cobre as 3 janelas — `get_upcoming_fixtures_with_kickoff` lê jogos NS e o `OddsExtractor` bucketa cada um pela proximidade do kickoff (`FUTEBOL_ODDS_WINDOWS` = bandas `t24h`:(1320,1440)min, `t1h`:(30,60)min, `t15m`:(0,15)min — esta a linha de FECHAMENTO p/ CLV real; banda inclusiva [0,15] + poll */15 garante captura ~T-15min), bate `/odds` 1x e grava 1 arquivo por (fixture, janela); **skip-if-exists** (1 snapshot/janela) e **não grava vazio** (jogo sem odds re-tenta). Client `get_odds` **paginado** (merge de `bookmakers[]` entre páginas, como `get_players`). Guarda **TODAS as casas** (filtrar não economiza quota — o custo é a chamada; Sportingbet/KTO nem aparecem no feed; Pinnacle=4 é a sharp de referência, Bet365=8) → external `raw_futebol_odds` (schema explícito; `bets`/`values` REPEATED aninhados; `odd`/`value` STRING — autodetect estragaria; `collection_timestamp`/`api_update` ISO→TIMESTAMP, `kickoff_timestamp` unix INT; key `odds` em `array_keys`) → dbt `stg_futebol_odds` (UNNEST 2× bets→`values` (keyword, crases); `SAFE_CAST(odd)`→FLOAT64) → `fact_odds_snapshot` (table, part. `collection_date`=`DATE(collection_timestamp)`, cluster `fixture_id,bookmaker_name`, **self-contained**, afunila p/ os **8 mercados-alvo** `market_id IN (1,4,5,6,7,8,10,12)` = Match Winner/Asian Handicap/Goals O/U/Goals O/U 1ºT/HT-FT/BTTS/Exact Score/Double Chance mantendo TODAS as casas, `odd_decimal > 1.0` invariante, `minutes_to_kickoff` = lead exato, `outcome_side`+`line_value` destrincham o `outcome_label` (O/U e Asian Handicap pareáveis por linha, sem parse frágil no app), dedup `fixture_id,bookmaker_id,market_id,outcome_label,collection_window` latest-wins). Brasileirão (71) **e** Copa do Mundo (1) 2026 — ambos `coverage.odds=TRUE` (≠ /injuries; Brasileirão pausado pela Copa, então os jogos correntes são todos da Copa). Service `extract-odds` deployado, imagem `dbt_futebol` rebuildada + job atualizado, `workflow_futebol_odds.yml` deployado (+ `+fact_odds_snapshot` no `--select` do workflow diário como rede de segurança) + Scheduler criado. **Pós-validação (PM, 2026-06-18):** 3ª janela **t15m** (0,15 — linha de FECHAMENTO p/ CLV real) + colunas derivadas **`outcome_side`**/**`line_value`** (destrincham o `outcome_label`: O/U e Asian Handicap pareáveis por linha sem parse no app; regex validado vs. dataset real, NULL só em composto/placar). dbt run/test 100% verdes (19 testes). Deploy feito 2026-06-18: imagem `dbt_futebol` rebuildada + job `dbt-futebol` re-fixado (não dropa as colunas no rebuild do poll) e `extract-odds` redeployado (revision 00002, t15m ativo). | implementado |
| 14 | `fact_predictions_api` | **BASELINE de comparação** (não é produto): a previsão pré-jogo do algoritmo da própria API via `/predictions?fixture=` — referência p/ avaliar um modelo próprio depois (se batermos a API consistentemente = edge real). 1 chamada por jogo numa única janela **T-2h** (a API atualiza de hora em hora; pegamos perto do kickoff). **FORWARD-ONLY** (previsão de jogo passado não é reconstruível — a API recomputa com o resultado já conhecido; sem backfill). Poll ~15min via `workflow_futebol_predictions.yml` (Scheduler `futebol-predictions-pregame`, SA `schedulerde@`), espelhando odds/lineups: `get_upcoming_fixtures_with_kickoff` lê jogos NS e o `PredictionsExtractor` bucketa cada um pela proximidade do kickoff (`FUTEBOL_PREDICTIONS_WINDOWS` = banda `t2h`:(100,130)min, tunável), bate `/predictions` 1x e grava 1 arquivo por fixture; **skip-if-exists** (1 captura/jogo) e **não grava vazio** (jogo sem previsão re-tenta). Client `get_predictions` **NÃO paginado** (`response` traz 1 elemento). Guarda só `predictions` (winner/win_or_draw/under_over/goals/advice/percent) e `comparison` (form/att/def/poisson_distribution/h2h/goals/total) — `teams`/`league`/`h2h` descartados (redundantes com fact_team_season_stats/fact_fixtures/fact_h2h) → external `raw_futebol_predictions` (schema explícito; `predictions`/`comparison` RECORD **aninhados**, **1 linha/fixture**; ⚠️ percentuais STRING "45%" — inclusive decimais "60.5%" — e linhas de gol/`under_over` STRING "-1.5" nullable, autodetect estragaria; `poisson_distribution` só home/away; `/predictions` não tem campo `update`; ⚠️ a key **NÃO** entra em `array_keys` do gcs_storage — payload de dicts aninhados cai no fallback e grava 1 linha JSON) → dbt `stg_futebol_predictions` (flatten por **dot-access** sem UNNEST; `SAFE_CAST(REPLACE(..,'%',''))`→FLOAT64; `def` em crases; `kickoff_utc` de unix) → `fact_predictions_api` (table, part. `collection_date`=`DATE(collection_timestamp)`, cluster `fixture_id`, **self-contained** competition de league_id, `minutes_to_kickoff`=lead exato, dedup `fixture_id` latest-wins). **Coverage**: `coverage.predictions=TRUE` p/ Brasileirão (71) **E** Copa do Mundo (1) — ambos incluídos (≠ /injuries; validado em dim_leagues 2026-06-17). Service `extract-predictions` deployado, imagem `dbt_futebol` rebuildada + job atualizado, `workflow_futebol_predictions.yml` deployado + Scheduler criado (+ `+fact_predictions_api` no `--select` do workflow diário como rede de segurança). Validado com 1ª captura real (England×Croatia, Copa): `comparison_*`/`prob_*` → FLOAT (prob soma 100), `comparison_total`="60.5%"→60.5; dbt run/test 100% verdes (23 testes). | implementado |
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
| BigQuery | `smartbetting-dados.futebol.*` (dataset novo, location `us-east1`; o plano original dizia `apifootball_raw`) |
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
