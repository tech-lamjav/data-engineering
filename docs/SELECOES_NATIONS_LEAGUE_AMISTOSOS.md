# Seleções — UEFA Nations League (5) e Amistosos (10)

> Mapa do trabalho necessário para ligar as duas competições de seleções no pipeline de futebol.
> Sondado contra a API viva em **2026-09-21** (plano Pro, 7.500 req/dia). Complementa
> `EXPANSAO_CAMPEONATOS_APIFOOTBALL.md`, que continua valendo como playbook geral de liga nova.
> **Estado em 21/09:** a trilha A (Nations League) virou os tickets DE#91, AE#193 e PPP#505.
> A trilha B (amistosos) foi decidida no grilling — ver **§4.0** e `docs/adr/0004-...`.
> As marcas ✅ indicam pergunta que era aberta e foi fechada; o texto original fica como registro.

---

## 1. TL;DR — são dois trabalhos diferentes, não um

| | **Nations League (5)** | **Amistosos (10)** |
|---|---|---|
| Natureza | rollout padrão do playbook | **não é rollout de liga** |
| Times na liga | 54, todas seleções principais | **441**, das quais só ~177 são seleções principais |
| Fixtures na season 2026 | 156, **todos NS** (24/09 → 17/11) | **726**, das quais **553 já finalizados** |
| Código novo | **zero** | **sim** — quatro peças, ver §4.0 |
| Odds | 9 casas com Pinnacle, 3 dias antes | existem, mas só de 7h a 46h do apito (ver §4.0) |
| Esforço | editar 2 arquivos de config + 6 `CASE` no dbt | investigação de produto antes de qualquer código |

**Recomendação de sequenciamento:** tratar a Nations League como um rollout do playbook e
decidir os amistosos separado. Bundlar os dois faz a Nations League perder a rodada 1.

**Relógio:** a rodada 1 da Nations League é **24/09 às 18:45 UTC**. A banda `t24h` de odds dispara
na noite de **23/09**. A banda `daily` (1–7 dias) já está aberta hoje. Para ter captura de odds
desde o começo, tudo precisa estar em produção **antes de 23/09** — e na ordem de §3.4, que é
**dbt antes da config**, não o contrário.

---

## 2. O que a API entrega (sondagem de 2026-09-21)

### 2.1 Coverage declarado (`/leagues`)

| Competição | id | season | odds | predictions | standings | xG / player stats | injuries | players |
|---|---|---|:--:|:--:|:--:|:--:|:--:|:--:|
| UEFA Nations League | 5 | 2026 | **T** | T | **T** | – ¹ | – | – |
| UEFA Nations League | 5 | 2024 | – | T | T | **T** | – | T |
| Friendlies | 10 | 2026 | **T** | T | – | T ¹ | – | T |

¹ O `FALSE` da Nations League 2026 em `events/lineups/statistics` é **pré-torneio**, o mesmo padrão
que a Premier League 2026 mostrou antes de 21/08 e que virou no kickoff (comentário em
`src/config.py:38`). A season 2024 da própria NL tinha tudo `TRUE`. Expectativa: flipa depois de
24/09 — **conferir em `dim_leagues` antes de tratar como disponível**.

### 2.2 Dados confirmados na marra (não só a flag)

- **Nations League**: `Netherlands x Germany` (24/09) devolve **9 casas com Pinnacle**, com
  Match Winner, Asian Handicap, Goals O/U e totais por time. `/odds?league=5&season=2026` devolve
  **5 páginas** — as duas primeiras rodadas já precificadas. `/predictions` responde.
  `/standings?league=5&season=2026` devolve **14 grupos** (Ligas A–D), formato que o
  `StandingsExtractor` já achata (`src/extractors/standings_extractor.py:70-90`), igual
  Libertadores e Copa do Mundo.
- **Amistosos**: `/odds?league=10&season=2026` devolve **3 jogos, todos de hoje**. Os testes
  individuais em `Japan x Uruguay` (24/09), `Australia x Brazil` (25/09) e
  `Argentina x Bolivia` (30/09) voltaram **`results: 0`**. As consultas por data para 22/09 e
  24/09 também voltaram zero.

  ✅ **RESPONDIDO em 21/09: é TARDIO, não esburacado.** Estes três parágrafos ficam como registro
  do que se observou primeiro, mas a leitura estava errada. A cobertura no dia do jogo foi 3 de 3,
  com Pinnacle, incluindo Dominica x Anguilla. O zero a três dias era artefato da retenção de ~7
  dias da API somada à ausência de amistosos naquele intervalo. Publicação entre 7h e 46h do apito.
  Ver §4.0.

### 2.3 A composição da liga 10 é o problema

`/teams?league=10&season=2026` devolve **441 times**:

| Recorte | Quantidade |
|---|---|
| Seleções principais (sem U##, sem feminino) | 177 |
| Categorias de base e femininas | 198 |
| Clubes (LA Galaxy, DC United, Club Tijuana, Pakhtakor…) | 66 |

E o campo `national` **não serve de filtro**: `São Tomé e Príncipe` vem com `national: false`,
enquanto `Albania U21` vem com `national: true`. Pior: **4 times que aparecem em fixtures não
existem no `/teams`** (`Anguilla`, `Dominica`, `Russia U18`, `Northern Ireland U19`) — ou seja,
um filtro por lista de times deixa buracos e o FK de `dim_teams` fica exposto.

Dos 726 fixtures da season, **346 têm seleção principal dos dois lados** pelo critério
nome + `national`. Os outros 380 são base, feminino ou clube.

---

## 3. Trilha A — Nations League: rollout padrão

### 3.1 `data-engineering/src/config.py` — as 15 listas

O invariante é testado em `tests/test_config_ligas_futebol.py:1-17`. Três grupos, semânticas
diferentes:

**Espinha (igualdade obrigatória com `LEAGUES_*`, teste em `:46-63`):**
`LEAGUES_BACKFILL` `:157` · `LEAGUES_CURRENT` `:183` · `TEAMS_BACKFILL` `:200` ·
`TEAMS_CURRENT` `:226` · `PLAYERS_BACKFILL` `:243` · `PLAYERS_CURRENT` `:269` ·
`FIXTURES_BACKFILL` `:287` · `FIXTURES_CURRENT` `:313`

**Opt-in por coverage (subconjunto, teste em `:69-79`):**
`STANDINGS_BACKFILL` `:360` · `STANDINGS_CURRENT` `:384` → **entram** (NL tem standings)
`INJURIES_BACKFILL` `:432` · `INJURIES_CURRENT` `:449` → **ficam fora** (coverage FALSE)

**Polls pré-jogo (ids puros, todo id tem que estar em `LEAGUES_CURRENT`, teste em `:85-91`):**
`FUTEBOL_ODDS_LEAGUE_IDS` `:553` · `FUTEBOL_PREDICTIONS_LEAGUE_IDS` `:608` → **entram**
`FUTEBOL_INJURIES_LEAGUE_IDS` `:650` → **fica fora**

✅ **RESOLVIDO (DE#91, Decisão 3): o backfill de 2024 fica FORA.** Texto original abaixo.

❓ **Backfill da season 2024?** A NL 2024 tem coverage cheio (xG, player stats, standings) e
custaria ~600 chamadas. O playbook §8.4 pede backtest de ≥1 rodada antes de sinalizar
oportunidade — e a season 2026 não tem nenhum jogo passado para backtestar. Ou a rodada 1 sai sem
backtest, ou o backtest roda sobre 2024. **Decide se o backfill é obrigatório ou dispensável.**
Atenção: a season 2024 da NL vai até **2026-03-31** pelo `/leagues`, então ela também tem o efeito
retroativo no histórico PIT descrito em §4.2 — menor que o dos amistosos, mas não zero.

### 3.2 `analytics-engineering/dbt_futebol` — 6 `CASE` + 6 `accepted_values`

Hoje são exatamente **6 marts** com `CASE requested_league_id`, 13 `WHEN` cada:

| arquivo | linhas |
|---|---|
| `models/marts/fact_fixtures.sql` | 11–25 |
| `models/marts/fact_odds_snapshot.sql` | 13–27 |
| `models/marts/fact_standings_snapshot.sql` | 13–27 |
| `models/marts/fact_injuries_snapshot.sql` | 13–27 |
| `models/marts/fact_predictions_api.sql` | 13–27 |
| `models/marts/fact_team_season_stats.sql` | 15–29 |

E 6 `accepted_values` (todos em `models/marts/models.yml`, todos `tags: ['guarda']`):
`:166` · `:777` · `:930` · `:1057` · `:1158` · `:1286`.

**O modo de falha é bom:** o `ELSE 'unknown'` faz o id esquecido virar a string `'unknown'`, que
não está na lista → as 6 guardas **falham alto**. Não é silêncio.

✅ **RESOLVIDO: `nations_league` e `amistosos`.**

❓ **Slug.** Sugestão `nations_league` e `amistosos`. O slug atravessa mart, serving, app e
nome de arquivo de brasão — trocar depois é migração, não rename.

### 3.3 O que **não** muda (para não inventar trabalho)

- **Nenhum extractor.** Zero `if league_id == X` em `src/`. Todos iteram as tuplas de config.
- **Nenhum workflow YAML.** Os `--select` enumeram **modelo**, não liga; nenhum YAML contém
  league_id. **`deploy_workflows.sh` não é necessário** se nenhum YAML mudar.
- **Nenhuma tabela externa do BigQuery.** Globam `*.json`; arquivo novo entra sozinho.
- **Nenhum seed.** Os 2 seeds (`futebol_teto_nota_contexto`, `futebol_p95_nota_contexto`) têm
  granularidade `(market, lado)` — **zero granularidade por competição**.
- **Nenhuma RPC e nenhuma migration de serving.** `get_futebol_competitions()` deriva a lista
  dinamicamente de `fact_fixtures.competition` (`docs/contrato-serving-gerado.md:75`).
- **Nenhuma lista do sync.** `FUTEBOL_SYNC_TABLES_ORDERED` (`src/config.py:781`) é lista de
  **tabelas**. Competição nova aumenta volume nas 22 tabelas, não muda escopo.

### 3.4 Ordem de deploy — **dbt primeiro, config depois**

Não existe ordem numerada escrita no repo, e o reflexo natural (mexer na config primeiro) está
**errado** aqui:

1. `CASE` + `accepted_values` no `analytics-engineering`
2. `./build-and-push.sh dbt_futebol` — já faz o `gcloud run jobs update` junto (`:6-13`)
3. `src/config.py` (as 15 listas)
4. `./scripts/deploy_cloud_run.sh futebol` — **obrigatório**: o deploy copia `src/` para dentro de
   cada serviço, então editar config sem redeployar não muda nada em produção
5. `deploy_workflows.sh` **só se algum YAML mudar** (não é o caso)

**Por que nesta ordem.** A Nations League **já tem odds** (5 páginas hoje). No instante em que a
config subir, o poll de odds roda a cada ~15min e a sua própria fase dbt roda junto. Com imagem
velha, `fact_odds_snapshot` calcula `competition = 'unknown'` e o **funil grava a linha**:
`fact_value_funnel` é `incremental` + `merge`, **append-only congelado no apito**
(`models/marts/fact_value_funnel.sql:2-4`), carrega `competition` (`:107`) e não a tem na
`unique_key`. Enquanto o kickoff está no futuro o merge corrige; **depois do apito a linha
congela com `'unknown'` para sempre**. É o mesmo desenho que tornou a linha-de-quarto irreversível.

Inverter não custa nada: um `WHEN 5` sem linha correspondente é inerte, e `accepted_values` só
avalia valor presente. Fazer dbt primeiro é grátis; fazer por último é apostar na janela.

⚠️ Efeito colateral útil: com a config subindo por último, as 6 guardas de `accepted_values`
nunca chegam a ficar vermelhas — elas só acenderiam durante a janela de inversão.

⚠️ O lembrete operacional em `EXPANSAO_CAMPEONATOS_APIFOOTBALL.md:78-81` ainda descreve o
build-and-push como dois comandos. Está desatualizado — hoje o script faz os dois.

### 3.5 Custo de API (estimativa derivada da config, não medida)

Em semana de rodada, com 26 jogos:

| Item | Chamadas |
|---|---|
| Espinha diária (leagues, teams, players, fixtures, standings) | 5/dia |
| Odds banda `daily` — 7 blocos/dia × 7 dias de horizonte | ~180/dia |
| Odds bandas de fechamento (t24h, t1h, t15m) — skip-if-exists acerta de primeira porque há preço | ~78 no dia da rodada |
| Predictions (banda diária de 14d + t2h) | ~50/dia |
| Per-fixture pós-rodada (4 endpoints × 26 jogos + refetch de 3 dias) | ~310 espalhados |
| Lineups pré-jogo | ~26/rodada |

Pico estimado **600–700/dia** contra 7.500. Folgado. Consumo de hoje: 892.

---

## 4. Trilha B — Amistosos: o que precisa ser decidido antes de escrever código

### 4.0 Decisões fechadas no grilling de 21/09 — **entendimento compartilhado, fronteira vazia**

> Registro formal em `docs/adr/0004-amistosos-como-competicao-de-insumo.md`.
> Vocabulário novo em `CONTEXT.md`: **competição de produto**, **competição de insumo**,
> **universo de uma competição de insumo**.

| # | Decisão | Escolha |
|---|---|---|
| 1 | **Para que servem os amistosos** | **Insumo**, não produto. Alimentam forma e histórico das seleções. Não geram oportunidade. |
| 2 | Efeito retroativo no histórico PIT | Aceitável **se medido antes**. |
| 3 | Teto de custo | **Zero custo recorrente.** O desenho evita a re-tentativa perpétua, não a absorve. |
| 4 | Janela alvo | **Novembro** (12 a 17). |
| 5 | Universo | **Times que o pipeline já conhece por outras competições**, avaliado depois do rollout da Nations League. Corta de 726 para **139** fixtures (115 finalizados). |
| 6 | Onde o filtro mora | **Um filtro só, na extração.** A raw deixa de ser cópia fiel; o precedente da meia-linha em quatro cópias pesou mais. |
| 7 | Quanto passado entra | **Temporada 2026 inteira**, 115 jogos. Nada anterior: `ultimos_10` não tem corte de recência. |
| 8 | Aparece no app | **Sim, normalmente.** Sem oportunidade, e sem botão de aposta (o CTA exige linha de mercado). |
| 9 | Invariante de config | **Exceção nominal.** A liga fica fora de `TEAMS_*` e `PLAYERS_*` (134 páginas/dia de catálogo inútil). |
| 10 | Estabilidade do universo | **União append-only.** Time que entrou nunca sai. |
| 11 | Régua do efeito retroativo | **0,25 pontos percentuais**, herdada da task [F] (#92). |
| 12 | Vocabulário | **Nomear no glossário**, não marcar no dado. |
| 13 | Onde mora o estado do universo | **Arquivo no GCS + guarda de crescimento.** Bootstrap cai para o arquivo de teams do mesmo run. |
| 14 | Forma da exceção | **Constante nominal** em config. Perfil por liga fica para depois. |
| 15 | Slug nos marts | **Só em `fact_fixtures`.** `WHEN` nos outros cinco contaria cobertura que não existe. |
| 16 | Como medir sem causar | **Target `futebol_taskF`**, nunca produção. |
| 17 | Per-fixture | **Gate de liga novo.** A forma lê só `fact_fixtures`; os quatro fatos per-fixture não entram. |
| 18 | Slug | **`amistosos`**. |

**Código novo que a decisão implica** (nada disso existe hoje): filtro de universo no
`FixturesExtractor`, gate de liga no `PerFixtureExtractor`, estado append-only em GCS com guarda de
crescimento, e a exceção nominal no invariante de `test_config_ligas_futebol.py`.

**Duas implicações que só apareceram na revisão do entendimento, e que a spec tem de carregar:**

1. **`assert_per_fixture_coverage_anomala` reprova no dia 1.** É `severity='error'`, roda sob
   `tag:guarda` no workflow diário, tolera 10% e tem blocklist de apenas
   `['copa_do_brasil','champions_league']` (`:43`). A decisão 17 garante **0%** de cobertura em 115
   fixtures. `amistosos` entra na blocklist **no mesmo commit** do slug. E o critério escrito no
   teste ("mata-mata cujas fases iniciais a API não cobre") deixa de descrever a lista, porque
   amistosos entram por decisão nossa, não por lacuna da API.
2. **A escalação pré-jogo escapa do gate da decisão 17.** `fixture_lineups_extractor` lê
   `get_upcoming_fixture_ids(45)` (`gcs_storage.py:596-644`), que filtra só por status e janela, sem
   olhar liga. Os 22 jogos futuros puxariam uma chamada cada a T-45min. Estender o gate a esse leitor
   é uma linha; a spec decide se estende ou aceita.

   ✅ **RESOLVIDO (DE#94, 2026-09-22): estendido, não aceito.** O teto de custo da decisão 3 é
   zero recorrente — aceitar as ~22 chamadas/T-45min contradiria a própria decisão. Ver
   `docs/adr/0004-amistosos-como-competicao-de-insumo.md`, seção "Implementação (DE#94)".

O **coletor ao vivo do `data-engineering`** recolhe a liga sozinho ao entrar em `FIXTURES_CURRENT`,
e isso é desejável: é o que assenta o placar final no mesmo dia. Não confundir com o coletor do
Supabase (`public.leagues_config`), que fica de fora por não haver aposta a liquidar.

⚠️ **Correção de fato (21/09):** a §2.2 dizia que os amistosos podiam não ter odds. **Falso.** O
veredito medido é **tardia, não esburacada**: a API cobre amistosos, inclusive Dominica x Anguilla,
com Pinnacle, e a cobertura no dia do jogo foi 3 de 3. O "zero no passado" era artefato da retenção
de ~7 dias da API, e não havia nenhum amistoso nessa janela. A publicação acontece entre **7h e 46h**
do apito, contra 3 a 4 dias das competições oficiais.

Consequência que sobrevive à correção: as bandas de `FUTEBOL_ODDS_WINDOWS` são **globais, não por
liga**, e a banda `daily` (1 a 7 dias) é estruturalmente vazia para amistoso. Fazer amistoso virar
produto exigiria janela de odds por liga, que não existe. Foi uma das razões da Decisão 1.

Teste de falsificação pendente, barato: `/odds?fixture=1628995` (Japan x Uruguay) cerca de 2h antes
do apito em 24/09.


### 4.1 O custo de ligar a liga 10 sem filtro

`/fixtures?league=10&season=2026` devolve **726 jogos, 553 já finalizados**. E o
`PerFixtureExtractor` **não tem gate de liga**: ele lê `get_fixture_ids_from_storage`
(`src/storage/gcs_storage.py:500-534`), que devolve **todo** fixture FT/AET/PEN do arquivo, sem
olhar liga. A condição de pular é `blob.exists() and not is_recent`
(`src/extractors/per_fixture_extractor.py:86`) — jogo **sem blob é chamado independente da data**.

Consequências diretas, em ordem de gravidade:

1. **~2.200 chamadas no primeiro run** (553 jogos × 4 endpoints: statistics, events, lineups,
   player stats). Cada serviço itera os 553 sozinho: `sleep(0.4)` dá ~3,7min só de espera, e com
   latência de API a passada fica na casa dos **7min**, contra timeout de **600s** do Cloud Run.
   Margem curta demais para confiar.
2. **Custo recorrente permanente.** Jogo FT que volta vazio **não é gravado**, de propósito, para
   ser re-tentado (`per_fixture_extractor.py:93-100`). Amistoso de base, feminino e os 46 jogos
   `CANC` provavelmente não têm statistics — e passam a ser re-buscados **todo dia, para sempre**.
   Estimativa grosseira: 200–400 jogos × 4 endpoints = **800–1.600 chamadas/dia queimadas em
   nada**, ~20% da cota.
3. **`/teams/statistics` semanal sobre 441 times.** O `TeamSeasonStatsExtractor` faz 1 chamada por
   linha do arquivo de teams (`team_season_stats_extractor.py:101`), sem filtro.
4. **Odds sobre jogo sem preço.** Na banda `daily` são 7 blocos/dia × ~6 dias ≈ **40–50 tentativas
   por fixture**, mais ~11 nas bandas de fechamento (que não gravam vazio e por isso re-tentam) —
   ~290 por rodada de amistosos só no fechamento, contra ~78 na Nations League, que tem preço.
5. **`/players?league=10&season=2026` é paginado e o coverage de players é TRUE.** Com 441 times,
   o catálogo pode ser dezenas de páginas **por dia**. Não medido — sondar antes de ligar.
6. **Volume no Supabase DEV.** O DEV estourou o free tier há duas semanas e a causa raiz (sync com
   escopo cheio para o DEV) segue sem correção — o purge é band-aid. 553 jogos de amistoso ×
   player stats e events caem exatamente nas tabelas que encheram o disco.

### 4.2 O efeito retroativo no histórico — o item mais delicado

Desde a **#91 / ADR 0010**, os defaults de produção da forma são `pit_escopo: todas` +
`pit_recorte: ultimos_10` (`models/intermediate/int_futebol_team_form_pit.sql:32-42`). **A forma
atravessa competições.**

Isso tem duas faces:

- **A favor:** as seleções **já têm histórico** no sistema, vindo da Copa do Mundo 2026
  (`copa_mundo`, `WHEN 1` em `fact_fixtures.sql:13`). A Nations League não estreia com forma zero.
- **Contra:** ligar os amistosos joga **553 jogos passados** (30 em janeiro, 158 em março, 218 em
  junho) dentro do histórico PIT das seleções — e isso **muda retroativamente a forma calculada
  para jogos de Copa do Mundo que já estão no mart**. Baselines congelados e medições da task [F]
  leem esse histórico.

  Guarda diretamente exposta: `tests/assert_pit_first_game_has_no_history.sql`, cuja partição em
  produção é `team_id` sozinho (`:11-14`).

✅ **RESOLVIDO (§4.0, decisão 7): a season 2026 inteira, 115 jogos sob o universo (B).**

❓ **Decidir:** se os amistosos entram só daqui para frente ou com a season inteira. O
`FixturesExtractor` em modo `current` puxa a season inteira — "só daqui para frente" **é código
novo**, não configuração.

### 4.3 Onde caberia o filtro

Não existe hoje **nenhum** mecanismo de exclusão por time, nome, categoria ou gênero, nem em
`src/` nem nos modelos dbt. Seria código novo. Em ordem de blast radius decrescente:

1. **`FixturesExtractor.extract`** (`src/extractors/fixtures_extractor.py:152-180`) — único
   estrangulamento real. O arquivo de fixtures é a fonte de per-fixture, odds, predictions,
   injuries pré-jogo, lineups pré-jogo e do coletor live. Filtrar aqui corta tudo de uma vez, e
   cada `item` já traz `teams.home.name` / `teams.away.name`.
2. **`GCSStorage`**, nos três leitores (`:500`, `:596`, `:650`) — corta o consumo, mantém a linha.
3. **Nos 3 polls** (odds `:166`, predictions `:123`, injuries `:253`) — deixa per-fixture pagando.
4. **dbt** — corta o produto, **não corta chamada de API nenhuma**.

✅ **RESOLVIDO (§4.0, decisões 5 e 6): times já conhecidos por outras competições, filtro único na extração. A regra por nome foi RECUSADA.**

❓ **Qual é a regra do filtro?** Nome com `U##` é fácil; `São Tomé` com `national: false` e os 4
times ausentes do `/teams` não são. Uma allowlist de ids de seleção principal é auditável mas
precisa de manutenção.

### 4.4 Guarda que acende

`tests/assert_per_fixture_coverage_anomala.sql:43` tem blocklist `['copa_do_brasil',
'champions_league']` e tolerância de 10% por `(competition, season, fato)`. O comentário do
próprio teste (`:36-38`) diz que competição nova fica dentro e acende de propósito. Com amistosos
de cobertura parcial, **acende vermelho**.

---

## 5. Efeito no Motor de Score (vale para as duas)

O board **não filtra competição** — não há allowlist em `fact_value_funnel.sql`,
`fact_value_opportunities.sql` nem `fact_value_funnel_selo.sql`. Competição nova entra sozinha.
O que muda é a **qualidade** do score:

| Premissa | Nations League | Amistosos |
|---|---|---|
| `sem_rodizio` (AH) | não dispara — não é pontos corridos, e a allowlist `macros/ligas_pontos_corridos.sql:15-20` é opt-in. **Ficar de fora é silêncio, não erro** | idem |
| `superioridade_tabela` (1X2, peso 6) | **sem gate de competição** (`int_futebol_premissas_1x2.sql:299-300`). O ramo `rank >= 6` nunca dispara em grupo de 4 times, mas **o ramo de `ppg` dispara sobre 2 a 6 jogos** | inerte (sem standings → rank/ppg NULL) |
| Premissas de xG | não disparam até o coverage flipar depois de 24/09 | dependem do jogo |
| Desfalques | não disparam (injuries FALSE) | idem |

⚠️ **Não existe piso global de amostra em produção.** Os pisos são por premissa e desiguais
(AH exige `>= 5` jogos, DC/OU/BTTS exigem `>= 3`), e **as premissas de percentual não têm piso
nenhum** (`btts:100-103`, `ou:307-310`, `dc:159` usam `SAFE_DIVIDE` sobre `played_total`). Um time
com 1 jogo produz 100% ou 0% e a premissa acende. É o ponto exposto em competição de poucos jogos
por time — e foi deixado por fazer de propósito (`taskf_pisos.sql:15`).

**Denominador da nota:** vem do seed `futebol_teto_nota_contexto` com granularidade
`(market, lado)`. **Competição nova não precisa de linha nova.**

---

## 6. Lado app — ticket para o Victor (repo `prop-play-predictor`)

Dois níveis, que não devem ir no mesmo balaio:

**Funcional — sem isso o placar ao vivo e a liquidação não cobrem a competição:**
- linha em `public.leagues_config` (tabela criada na migration `082_multi_league_collector.sql`),
  com `enabled = true`. É ela que decide o que o coletor multi-liga varre.

**Cosmético — degrada com elegância se ficar de fora:**
- `src/utils/futebol-competitions.ts`: `COMPETITION_LABELS` `:11` (sem isso o rótulo cai no
  `humanize`), `COMPETITION_API_IDS` `:35` (sem isso, ícone de troféu no lugar do brasão),
  `ALL_COMPETITIONS` `:52` (ordem no seletor; desconhecida cai no fim, alfabética)
- `supabase/functions/mirror-futebol-league-logos/index.ts` — mapa `LIGAS` `:46`
- `supabase/functions/mirror-futebol-player-photos/index.ts` — `DEFAULT_PAIRS` `:38`

O próprio arquivo do app diz que é data-driven: "Liga nova que o Mateus subir aparece sozinha nas
telas" (`futebol-competitions.ts:3-5`).

---

## 7. Calendário e dormência

- **Nations League 2026**: 24/09 → 17/11, 6 rodadas de 26 jogos. Depois disso, **dormente até
  março de 2027**.
- **Amistosos**: a season 2026 fecha em 17/11. As janelas FIFA são intermitentes o ano todo.

✅ **RESOLVIDO (DE#91, Decisão 4): a NL segue o padrão da Copa e fica nas listas `CURRENT`.**

❓ A Copa do Mundo (id 1) já estabeleceu o padrão de competição dormente: fica nas listas
`CURRENT` e as chamadas diárias voltam vazias. **Decidir se a NL segue esse padrão** (5
chamadas/dia queimadas entre novembro e março, o que é irrelevante) **ou se sai das listas fora de
janela** (mais limpo, mais manutenção).

---

## 8. Checklist de verificação antes de ligar

1. Reconferir `dim_leagues` depois de 24/09 para ver se `events`/`lineups`/`statistics` da NL
   flipou para TRUE.
2. Medir odds de amistoso **dentro da janela** (manhã de 25/09, `Australia x Brazil`) antes de
   concluir qualquer coisa sobre a trilha B.
3. Confirmar Pinnacle presente nas odds de NL na hora do t24h (já confirmado a 3 dias).
4. Rodar `dbt build --select +fact_value_opportunities` e conferir `competition` correta,
   evidências das premissas e `faixa` coerente.
5. Backtest RPS/CLV de ≥1 rodada antes de sinalizar oportunidade ao usuário (playbook §8.4).

---

## 9. Drift encontrado de passagem (não bloqueia, mas está errado hoje)

- `workflow_futebol_team_stats.yml:11` — header diz "Brasileirão 2026 + Copa 2026"; são 13 ligas.
- `docs/ARQUITETURA_DATA_ENGINEERING.md:406-415` — tabela de schedulers não lista
  `workflow-futebol-fixtures-live` nem `workflow-futebol-sync`.
- `src/config.py:529-532` — comentário diz "1 chamada por fixture por dia" na banda diária de
  odds; são até 7 (é anterior ao PPP#366).
- `models/marts/models.yml:167` — descrição de `competition_id` para no `61 = Ligue 1`, falta o 94.
- `models/marts/dim_leagues.sql:3` e `models.yml:143` — descrições paradas em 6 e 11 competições.
- `workflow_futebol_odds.yml:299` — comentário fala "PASS=13 ERROR=1" contra ~51 guardas atuais.
- `EXPANSAO_CAMPEONATOS_APIFOOTBALL.md:78-81` — build-and-push descrito como dois comandos.
