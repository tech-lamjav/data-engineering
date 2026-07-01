# Expansão de Campeonatos — API-Football

> Investigação: quais outros campeonatos podemos extrair com a pipeline atual de futebol
> (data-engineering + analytics-engineering) e quais rendem **os mesmos resultados do
> Campeonato Brasileiro**. Validado empiricamente contra a API em **2026-06-23** (plano
> Pro ativo até 2026-07-11, 7.500 req/dia).

---

## 1. TL;DR

- **A arquitetura já está pronta.** A pipeline é 100% parametrizada por `(league_id, season)`
  em `src/config.py`. Adicionar um campeonato = editar listas de config + **2 linhas de
  `CASE`** no dbt. Zero código novo de extrator.
- **Cobertura de dados é praticamente idêntica à do Brasileirão para TODAS as grandes ligas.**
  Premier League, La Liga, Serie A (ITA), Bundesliga, Ligue 1, Primeira Liga, Eredivisie,
  Champions/Europa/Conference League e Libertadores/Sudamericana têm `statistics_fixtures`
  (xG), `statistics_players`, `standings`, `predictions` — e as 1ªs divisões europeias também
  têm `injuries` — **todos TRUE**. Estruturalmente, rendem o mesmo que o Brasileirão.
- **O flag `coverage.odds` é SAZONAL, não estrutural.** Ele só fica TRUE quando há jogos na
  janela de 1–14 dias pré-jogo (que é quando a API serve odds pré-jogo). Hoje as ligas
  europeias estão em recesso (voltam em **agosto/2026**), então `odds=false` — mas voltará a
  TRUE no início da temporada. **Isso significa que "obter os mesmos resultados" depende do
  calendário: cada liga precisa ser ligada na sua temporada.**
- **Recomendação:** adotar uma carteira **Brasil + Europa** para cobertura o ano todo, em 3 ondas
  (ver §6). Começar **já** por **Série B (72)** — está ao vivo hoje com 13 casas de odds — e pelas
  competições CONMEBOL; ligar as **europeias em agosto**, quando suas temporadas (e odds) abrem.

---

## 2. O que define "mesmos resultados do Brasileirão"

O produto final é a `fact_value_opportunities` (saída do **Motor de Score de Confiabilidade**,
ver `analytics-engineering/docs/MOTOR_SCORE_CONFIABILIDADE.md`). Para o Motor pontuar como no
Brasileirão, a liga precisa do `coverage` abaixo (objeto retornado por `/leagues` por temporada):

| Sinal de cobertura | Endpoint | Para que o Motor usa | Sem ele… |
|---|---|---|---|
| `odds` ⭐ | `/odds` | **Núcleo de valor**: de-vig Pinnacle, edge, PTS_VALOR, corroboração de linha sharp | **Sem oportunidade** (gate exige edge>0 e ≥3 casas). Crítico. |
| `predictions` | `/predictions` | PTS_CORROBORACAO (`modelo_api_concorda`) | Perde +7 de corroboração |
| `statistics_fixtures` (xG) | `/fixtures/statistics` | Premissas de xG (1X2 `superioridade_xg`; O/U pace) | Premissas de xG não disparam (graceful) |
| `statistics_players` | `/fixtures/players` | Ratings/minutos por jogador (proxy de importância de desfalque, S7) | Proxy de desfalque enfraquece |
| `standings` | `/standings` | Premissas de tabela (`superioridade_tabela`, forma) | Não dispara (normal em mata-mata) |
| `injuries` | `/injuries` | Premissas de desfalque (1X2 `desfalque_adversario`, penalidade `desfalque_proprio`) | Não dispara (igual à Copa do Mundo hoje) |
| `players` (catálogo) | `/players` | Dimensão de jogadores | Fallback via fixture players |

**Conclusão:** `odds` é a condição **necessária** (sem ela não há produto). xG/injuries são
"enriquecedores" — sua ausência **degrada graciosamente** o score (premissas viram FALSE), não
quebra a pipeline.

---

## 3. Como a pipeline trata um novo campeonato (esforço real)

Tudo confirmado no código:

1. **`data-engineering/src/config.py`** — adicionar o ID e incluí-lo nas tuplas de cada endpoint:
   `LEAGUES_*`, `TEAMS_*`, `PLAYERS_*`, `FIXTURES_*`, `STANDINGS_*` (sempre);
   `INJURIES_*` **só se `coverage.injuries=TRUE`**; `FUTEBOL_ODDS_LEAGUE_IDS` e
   `FUTEBOL_PREDICTIONS_LEAGUE_IDS` **só se os respectivos flags forem TRUE**. Os extratores
   iteram as tuplas automaticamente — **nenhum extrator muda**.
2. **dbt — 2 lugares apenas** (mapeiam `league_id → competition`):
   - `analytics-engineering/dbt_futebol/models/marts/fact_fixtures.sql:11-14`
   - `analytics-engineering/dbt_futebol/models/marts/fact_team_season_stats.sql:15-19`
   Acrescentar um `WHEN <id> THEN '<slug>'` em cada. Todo o resto do dbt flui pela coluna
   `competition` (sem hardcode).
3. **Tabelas externas BQ**: globam `*.json` no GCS — novos arquivos entram sozinhos.
4. **Workflows/scheduler**: já orquestram todas as tuplas de config; nada a mudar para a coleta
   diária. (Backfill é disparo manual `mode=backfill`.)
5. **(Opcional, recomendado) Recalibrar limiares das premissas** — ver §7.

Lembrete operacional (memória do projeto): mudar modelos dbt_futebol exige
`build-and-push.sh dbt_futebol` + `gcloud run jobs update`; mudar workflow exige
`deploy_workflows.sh`. Editar só o YAML/local não muda o que roda em produção.

---

## 4. Cobertura real por campeonato (sondado em 2026-06-23)

Legenda: **xG** = statistics_fixtures · **pl** = statistics_players · **st** = standings ·
**inj** = injuries · **pred** = predictions · **odds** = odds. (T=true · –=false)

### 4.1 Referência

| Liga | id | xG | pl | st | inj | pred | odds |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|
| **Brasileirão Série A** (atual) | 71 | T | T | T | T | T | T |
| Copa do Mundo (atual; encerra ~19/07) | 1 | T | T | T | – | T | T |

### 4.2 Tier A — Paridade TOTAL (inclui injuries; odds abre na temporada, ago/2026)

Europa, 1ª divisão — **estruturalmente idênticas ao Brasileirão**:

| Liga | id | xG | pl | st | inj | pred | Início temporada |
|---|---|:--:|:--:|:--:|:--:|:--:|---|
| Premier League (ING) | 39 | T | T | T | T | T | 21/08/2026 |
| La Liga (ESP) | 140 | T | T | T | T | T | ~ago/2026 |
| Serie A (ITA) | 135 | T | T | T | T | T | 23/08/2026 |
| Bundesliga (ALE) | 78 | T | T | T | T | T | ~ago/2026 |
| Ligue 1 (FRA) | 61 | T | T | T | T | T | 22/08/2026 |
| Primeira Liga (POR) | 94 | T | T | T | T | T | ~ago/2026 |
| Eredivisie (HOL) | 88 | T | T | T | T | T | 07/08/2026 |
| **UEFA Champions League** | 2 | T | T | T | T | T | 07/07/2026 (quali) |
| **UEFA Europa League** | 3 | T | T | T | T | T | ~set/2026 |
| **UEFA Conference League** | 848 | T | T | T | T | T | ~set/2026 |

2ª divisões europeias com cobertura cheia (também úteis; mesmo perfil):
Championship `40`, League One `41`, League Two `42`, 2.Bundesliga `79`, 3.Liga `80`,
Serie B ITA `136`, Ligue 2 `62`, **Segunda División ESP `141`** (já com `odds=T` agora, por
causa dos playoffs), Eerste Divisie HOL `89`.

### 4.3 Tier B — Paridade FORTE (falta só `injuries` → premissas de desfalque não disparam, igual à Copa do Mundo hoje)

| Liga | id | xG | pl | st | inj | pred | odds | Janela |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|---|
| **Brasileirão Série B** | 72 | T | T | T | – | T | **T** | **AO VIVO hoje (13 casas)** |
| CONMEBOL Libertadores | 13 | T | T | T | –¹ | T | – | retoma 11/08 |
| CONMEBOL Sudamericana | 11 | T | T | T | – | T | – | retoma ago |
| Segunda Liga (POR) | 95 | T | T | T | – | T | – | ago |
| Paulista A1 (estadual) | 475 | T | T | T | – | T | – | jan–abr/2027 |
| Carioca-1 (estadual) | 624 | T | T | T | – | T | – | jan–abr/2027 |

¹ Libertadores teve `injuries=T` em 2025; em 2026 ainda `false` (não populado).

### 4.4 Tier C — Paridade PARCIAL (sem xG/player stats → Motor roda degradado; núcleo de valor por odds+tabela ainda funciona)

| Liga | id | xG | pl | st | pred | odds | Obs |
|---|---|:--:|:--:|:--:|:--:|:--:|---|
| Série C | 75 | – | – | T | T | T | só valor + tabela |
| Série D | 76 | – | – | T | T | T | só valor + tabela |
| **Copa do Brasil** | 73 | T | T | –² | T | –³ | tem xG/players! mas mata-mata |
| Estaduais 2ª divisão c/ odds | 613,619,936,1098… | – | – | T | T | T | nicho |

² Mata-mata não tem tabela (esperado). ³ `odds=false` no snapshot — **verificar dentro da janela
de jogo** (entre fases pode zerar). Copa do Brasil é alto interesse; vale rechecar em rodada ativa.

### 4.5 Tier D — Inviável para o produto

Maioria dos estaduais de divisões inferiores, sub-20/sub-17, femininos, várzea: têm só
`standings`/`predictions`, **sem odds e sem stats**. Não geram oportunidade de valor.

---

## 5. A descoberta-chave: `odds` é sazonal (evidência empírica)

Provas coletadas hoje (2026-06-23) que isolam calendário de cobertura:

- **Mesma liga, temporadas diferentes:** Brasileirão `71` → 2025 (encerrado) `odds=false`;
  2026 (em curso) `odds=true`.
- **Mesmo país, mesmo dia:** La Liga `140` (encerrada) `odds=false`; Segunda División `141`
  (playoffs em curso) `odds=true`.
- **Próximos jogos:** PL → 21/08; Serie A ITA → 23/08; Ligue 1 → 22/08; Eredivisie → 07/08;
  La Liga/Bundesliga/Primeira Liga → **sem jogos futuros** (recesso). Brasileirão Série B → **hoje**.
- **Odds ao vivo (consulta `/odds`):** Série B 2026 → 13 casas no 1º jogo; Série A 2026 → só 3
  casas (jogos a ~30 dias, fora do pico da janela); PL/La Liga/Ligue 1 2025 → **0** (recesso).

**Leitura:** a API serve odds pré-jogo ~1–14 dias antes da partida e as casas vão entrando
conforme o jogo se aproxima. Logo, `coverage.odds=false` para Europa **agora** é puramente
recesso — não limitação de cobertura. Em agosto, as europeias terão odds como o Brasileirão.

---

## 6. Recomendação — carteira Brasil + Europa, em 3 ondas

Objetivo: **cobertura o ano todo** (hoje o produto depende de Brasileirão A + Copa do Mundo, e a
Copa do Mundo encerra ~19/07, com a Série A pausada até 22/07 — há um buraco iminente).

Calendário de futebol: **Brasil** (ano-calendário) roda abr–dez; **Europa** (cruzada) roda
ago–mai; **estaduais** jan–abr. Combinar Brasil + Europa fecha o ano.

| Onda | Quando | Ligas | Por quê |
|---|---|---|---|
| **1 — agora** | imediato | **Série B (72)**, **Libertadores (13)**, **Sudamericana (11)** | ao vivo/calendário-ano; alto interesse BR; tapa o buraco da pausa da Série A e do fim da Copa do Mundo. Paridade forte (sem injuries). |
| **2 — pré-agosto** | jul/2026 | **Premier League (39)**, **La Liga (140)**, **Serie A ITA (135)**, **Bundesliga (78)**, **Ligue 1 (61)**, **Primeira Liga (94)**, **Champions League (2)** | paridade **TOTAL** (com injuries); cobrem a baixa do futebol brasileiro (dez–mar). Primeira Liga = público lusófono. |
| **3 — opcional** | conforme demanda | Eredivisie (88), 2ª divisões europeias, Copa do Brasil (73, verificar odds), Europa/Conference League (3/848) | profundidade de catálogo |

---

## 7. Riscos e cuidados

- **Recalibrar limiares das premissas (importante).** Os thresholds (médias de gols etc.) foram
  calibrados na Série A brasileira. Bundesliga (muitos gols) vs Serie A ITA (poucos gols) deslocam
  as premissas de **O/U** e força. Antes de confiar nos scores de uma liga nova, rodar backtest
  RPS/CLV (Fase 5 do Motor) e ajustar por liga/tier nos modelos `int_futebol_premissas_*`.
- **Pinnacle por liga.** O de-vig usa **Pinnacle (bookmaker_id=4)**. Confirmar que Pinnacle aparece
  nas odds de cada liga **dentro da janela** (Série B tem 13 casas → provável; validar). Sem
  Pinnacle, o núcleo de valor precisa de casa sharp alternativa.
- **Ligar `injuries` só onde `coverage.injuries=TRUE`** (Tier A e UCL/UEL sim; Série B,
  Libertadores 2026, Sudamericana, estaduais **não**) — senão são chamadas desperdiçadas e tabela vazia.
- **xG ausente nos tiers baixos** (Série C/D, estaduais menores): Motor roda sem premissas de xG —
  scores mais baixos/menos confiáveis. Aceitável, mas comunicar no produto.
- **Mata-matas não têm tabela** → premissa `superioridade_tabela` não dispara (graceful).
- **Cota de API**: folgada (493/7500 usados hoje). O pico é o **backfill** (~1.5k req por
  liga-temporada de fixture stats) — escalonar em dias. Diário em regime fica < 1k mesmo com
  várias ligas.
- **Custo BigQuery**: tabelas externas NDJSON sofrem **full scan a cada build** (sem predicate
  pushdown — ver memória do projeto). Mais ligas = scans maiores. O fator de custo é **frequência
  de build**, não filtro. Monitorar ao multiplicar ligas.
- **Copa do Mundo (id 1) fica dormente** após ~19/07 (volta só em 2030). Onda 1 a substitui.

---

## 8. Verificação (como testar de ponta a ponta)

1. **Reconfirmar cobertura na janela** de cada liga nova (script de sondagem em
   `/odds`+`/leagues` por `(id, season)`); em especial `odds` e Pinnacle dentro de 1–14 dias do jogo.
2. `mode=backfill` para 1 liga-temporada e conferir arquivos em
   `gs://smartbetting-landingzone/futebol/<endpoint>/`.
3. Acrescentar o `WHEN` nos 2 modelos dbt; `dbt build --select +fact_value_opportunities`
   (via `.venv` do analytics-engineering) e validar amostras: `competition` correta, evidências
   das premissas disparando, e `faixa` coerente.
4. Backtest RPS/CLV em ≥1 rodada antes de sinalizar oportunidades da liga ao usuário.
