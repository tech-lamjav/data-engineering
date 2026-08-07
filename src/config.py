"""Configurações centralizadas do projeto."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis de ambiente do arquivo .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# API Configuration
API_BASE_URL = "https://api.balldontlie.io/v1"
API_BASE_URL_V2 = "https://api.balldontlie.io/v2"  # Para endpoints v2
API_BASE_URL_NBA = "https://api.balldontlie.io/nba/v1"  # Para team_season_averages
API_BASE_URL_NBA_V2 = "https://api.balldontlie.io/nba/v2"  # Para stats/advanced game-by-game
API_KEY = os.getenv("BALLDONTLIE_KEY")
API_TIMEOUT = 60

# API-Football (futebol/soccer) — vertical paralela à NBA
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")

# Limiares de alerta de cota no resumo diário (src/reporting/api_quota.py). O /status da
# API-Football é a ÚNICA visibilidade de orçamento que existe: em 2026-08-05 o consumo
# estava em 7.189/7.500 (95,9%) e o plano vencia em 6 dias, e nada no pipeline reportava
# nenhum dos dois — a primeira notícia de cota estourada seria o produto vazio.
QUOTA_ALERT_PCT = 80.0  # consumo do dia acima disso destaca no e-mail (agir no mesmo dia)
SUBSCRIPTION_ALERT_DAYS = 14  # vencimento mais perto que isso destaca (renovação depende de terceiro)

# Ligas alvo do pipeline futebol
BRASILEIRAO_ID = 71
COPA_MUNDO_ID = 1  # validar no primeiro run
SERIE_B_ID = 72  # odds/predictions TRUE, injuries FALSE (coverage validado 2026-07)
COPA_DO_BRASIL_ID = 73  # mata-mata: standings/injuries FALSE; predictions TRUE; odds sazonal -> FASE 2 (validado 2026-07-10)
LIBERTADORES_ID = 13  # grupos+mata-mata: standings/predictions TRUE; injuries FALSE em 2026 (2025 foi TRUE — rechecar em ago); odds armada dia 0, dormente até 10/08 (validado 2026-07-13)
SUDAMERICANA_ID = 11  # par da Libertadores (grupos+mata-mata): standings/predictions TRUE; injuries FALSE em 2024/25/26 (exclusão simples, sem o caveat 2025 da Liberta); odds armada dia 0, dormente até t24h ~20/07 (R32 21–31/07; oitavas ida 11–13/08, volta 18–20/08) (validado 2026-07-14)
LA_LIGA_ID = 140  # 1ª europeia (top-5, pontos corridos 20 times): Tier A completo — standings/players/predictions/injuries + xG. Probe 2026-07-15: 2024/2025 c/ injuries/players/stats_fixtures=TRUE (coverage estrutural sólida); 2026 mostra FALSE por PRÉ-TEMPORADA (abertura 16/08), flipam ao começar. Split-year: season 2026 = 2026/27. Odds armada dia 0, dormente até t24h ~15/08 (opener 16/08)
PREMIER_LEAGUE_ID = 39  # 2ª europeia (top-5, pontos corridos 20 times): Tier A completo, espelho da La Liga. Probe 2026-07-17: 2024/2025 c/ injuries/players/stats_fixtures/events/lineups/standings/predictions=TRUE; 2026 mostra FALSE em xg/events/lineups/players/injuries por PRÉ-TEMPORADA (abertura 21/08), flipam ao começar — standings/predictions já TRUE. Split-year: season 2026 = 2026/27 (start 2026-08-21, end 2027-05-30, 380 jogos/20 times). Cobre a baixa do futebol BR (dez–mar). Odds armada dia 0, dormente até t24h ~20/08 (opener 21/08 19:00 UTC, Arsenal x Coventry; rodada 22/08)

# Onda 2 (interesse médio, iniciada 2026-07-28): europeias adicionais, conforme a
# temporada/odds de cada uma abre. Mesmo playbook da Onda 1 — ver
# data-engineering/docs/EXPANSAO_CAMPEONATOS_APIFOOTBALL.md §6.
UCL_ID = 2  # UEFA Champions League: formato liga única (36 times) desde 2024/25 + mata-mata.
# Probe 2026-07-28: 2026/27 (season=2026) já em fase classificatória desde 07/07 —
# odds=TRUE JÁ AO VIVO (13 casas incl. Pinnacle confirmadas em jogos de hoje, ver
# fixtures 1556505/1556507/1556508); predictions=TRUE. standings/injuries=FALSE
# ainda (fase liga de 36 times só forma tabela a partir de ~set/2026) — RECHECAR
# quando a fase de liga começar (mesmo caveat da Libertadores: pode flipar TRUE).
# 2024 (2024/25) e 2025 (2025/26) — ambas encerradas — confirmam standings/injuries/
# predictions/xG=TRUE, odds=FALSE (temporada encerrada, esperado). Split-year: season
# 2026 = 2026/27.
SERIE_A_ITA_ID = 135  # 3ª europeia (top-5, pontos corridos 20 times): Tier A completo, espelho da La Liga/PL.
# Probe 2026-08-03: 2024 (2024/25) e 2025 (2025/26) com events/lineups/stats_fixtures/
# stats_players/standings/players/predictions/injuries=TRUE (coverage estrutural sólida, idêntica
# à La Liga/PL); odds=FALSE nas duas (temporadas encerradas, esperado). 2026 (current=True) mostra
# FALSE em events/lineups/stats/players/injuries por PRÉ-TEMPORADA, flipam na abertura —
# standings/predictions JÁ TRUE. Split-year: season 2026 = 2026/27 (start 2026-08-22,
# end 2027-05-30, 380 jogos / 20 times, todos NS). Opener 22/08 16:30 UTC (Udinese x Como,
# Inter x Monza) -> odds t24h abre ~21/08; predictions pregame (janela 14d) engaja ~08/08;
# injuries pregame (janela 96h) engaja ~18/08 — as duas janelas deixaram de coincidir em 2026-08-06.
# ⚠️ NÃO recalibrar as premissas de O/U por causa desta liga: o ambiente de gols medido em 1.520
# jogos FT (24/25+25/26) dá 2,49 gols/jogo e 47,0% de Over 2.5 — praticamente idêntico ao
# Brasileirão (2,48 / 46,7%), que é onde os thresholds foram calibrados. A fama de "liga de poucos
# gols" é folclore do catenaccio e não se sustenta no dado; baixar os limiares aqui INTRODUZIRIA
# viés pró-Under. O desvio real está em Premier League (+0,36 gols/jogo, O2.5 +9,1pp) e Série B
# (-0,28, -8,2pp) — ver docs/EXPANSAO_CAMPEONATOS_APIFOOTBALL.md §7.
BUNDESLIGA_ID = 78  # 4ª europeia (top-5): Tier A completo. Probe 2026-08-05: 2024 (2024/25) e 2025
# (2025/26) com events/lineups/stats_fixtures/stats_players/standings/players/predictions/
# injuries=TRUE; odds=FALSE nas duas (temporadas encerradas, esperado). 2026 (current=True) mostra
# FALSE em events/lineups/stats/players/injuries por PRÉ-TEMPORADA — standings/predictions JÁ TRUE.
# Split-year: season 2026 = 2026/27 (start 2026-08-28, end 2027-05-22) -> odds t24h abre ~27/08;
# predictions pregame (janela 14d) engaja ~14/08; injuries pregame (janela 96h) engaja ~24/08.
# ⚠️ FORMATO DIFERENTE das outras top-5 (gabarito de verificação não é o mesmo):
#   - 308 fixtures/season, não 306 nem 380: 18 times x 34 rodadas + PLAYOFF DE REBAIXAMENTO de 2
#     jogos contra o 3º da 2. Bundesliga. A rodada extra vem como "Relegation Round" (24/25) e,
#     inconsistentemente, como "Final" (25/26) — é a API, não o nosso dado.
#   - 19 times distintos em /teams (18 da liga + o adversário do playoff). Como o catálogo já traz
#     os 19, NÃO há risco de órfão em dim_teams.
#   - 18 linhas em /standings, grupo único (sem o artefato de grupo da Liberta/Sudamericana).
# ⚠️ AMBIENTE DE GOLS = MAIOR DESVIO DO PORTFÓLIO (medido em 616 jogos FT, 24/25+25/26): 3,18
# gols/jogo (+0,70 vs Brasileirão) e 61,9% de Over 2.5 (+15,2pp) — quase o DOBRO do desvio da
# Premier League. Ao contrário da Serie A ITA, aqui o briefing "liga de muitos gols" se confirma.
# A liga entra SEM gate, e as premissas de Over/BTTS-Sim/AH vão disparar mais que em qualquer
# outra — comportamento esperado dos thresholds atuais, não defeito. Correção = recalibração por
# liga (Fase 5), com backtest RPS/CLV: ver MOTOR_SCORE_CONFIABILIDADE.md §12.2.1.

LIGUE_1_ID = 61  # 6ª europeia e ÚLTIMA da Onda 2: Tier A completo. Probe 2026-08-06: 2024
# (2024/25) e 2025 (2025/26) com events/lineups/statistics_fixtures/statistics_players/standings/
# players/predictions/injuries=TRUE; odds=FALSE nas duas (temporadas encerradas, esperado).
# 2026 (current=True) mostra FALSE em events/lineups/stats/players/injuries por PRÉ-TEMPORADA —
# standings/predictions JÁ TRUE. Mesma leitura de La Liga/PL/Serie A/Bundesliga; NÃO é o caso da
# UCL, onde o FALSE era estrutural da fase classificatória.
# Split-year: season 2026 = 2026/27, start 2026-08-21 -> end 2027-05-29.
# ⚠️ ABERTURA = 2026-08-21 18:45 UTC. A task do ClickUp dizia 22/08, foi "corrigida" p/ 23/08, e
# as duas estão erradas: a 1ª rodada tem 9 jogos espalhados em 21/08 (1), 22/08 (5) e 23/08 (3) —
# 23/08 é o FIM da rodada 1, não o começo. Daqui saem as duas datas operacionais:
# odds t24h abre ~20/08; predictions pregame (janela 14d) engaja ~07/08; injuries pregame
# (janela 96h) engaja ~17/08.
# ⚠️ GABARITO DE VERIFICAÇÃO SÃO TRÊS NÚMEROS, e nenhum é o das outras top-5:
#   - base 18 times x 34 rodadas = 306 — é assim que a TEMPORADA NOVA valida (confirmado: 306 NS
#     em 2026, todos Regular Season);
#   - 2024/25 = 308 (306 + 2 jogos de "Relegation Round"), 19 times distintos;
#   - 2025/26 = 310 (306 + 4: "Relegation round - Quarter-finals", "Semi-finals" e a final em 2
#     jogos rotulada "Final"), 21 times distintos.
#   O playoff de acesso/rebaixamento contra a Ligue 2 é um BRACKET DE TAMANHO VARIÁVEL que a API
#   anexa só no fim da temporada, e os rótulos mudam de ano p/ ano — é a API, não o nosso dado.
#   Um dos jogos de 25/26 é Ligue 2 x Ligue 2 (RED Star 93 x Rodez).
#   - /teams devolve 19 (2024) e 21 (2025): o catálogo JÁ traz todos → ZERO risco de órfão em
#     dim_teams (mesma leitura da Bundesliga, ≠ o incidente de standings da issue #12 do dbt).
#   - /standings: 18 linhas por season, grupo único.
#   - ⚠️ 1 fixture AWD em 25/26 (1388000, rodada 34, 0-0 de W.O.): fica FORA do spine
#     (FT/AET/PEN) e fora das premissas (que filtram FT), então não vira resultado falso — mas o
#     gabarito é "310 fixtures, 309 finalizados". Não ler 309/310 como buraco.
#   - ⚠️ os 6 jogos de playoff NÃO têm xG (expected_goals nulo nos sondados), mesmo padrão da
#     Bundesliga. xG ~100% nos 306 de pontos corridos.
# ⚠️ AMBIENTE DE GOLS (medido em 617 jogos FT, 24/25+25/26): 2,90 gols/jogo (+0,42 vs Brasileirão)
# e 54,1% de Over 2.5 (+7,4pp). A liga é a 2ª do portfólio em gols/jogo, atrás só da Bundesliga
# (3,18), mas a 3ª em taxa de Over 2.5, ATRÁS da Premier League (55,8%) — é o PRIMEIRO caso da
# carteira em que as duas ordens divergem. Assinatura de goleada, não de jogo aberto. Entra SEM
# gate e SEM recalibração; a divergência é evidência de entrada da Fase 5 (a medição está no
# ticket 8/9, analytics-engineering#31). Ver MOTOR_SCORE_CONFIABILIDADE.md §12.2.1.
# ⚠️ CADÊNCIA: esta liga leva LEAGUES_CURRENT/TEAMS_CURRENT ao 12º target. A API-Football derruba
# RAJADAS (~10 req rápidas) mesmo com cota sobrando, e leagues/teams eram os únicos extractors sem
# espera — o time.sleep(0.4) adicionado em 16/07 foi escrito exatamente p/ este caso. Todo laço
# novo sobre (league_id, season) nasce com o sleep.

PRIMEIRA_LIGA_ID = 94  # 7ª europeia e ÚLTIMA liga da Onda 2 (fecha a expansão). Probe 2026-08-07.
# ⚠️ COVERAGE NÃO É UNIFORME ENTRE AS DUAS TEMPORADAS FECHADAS — é o ponto que difere das outras
# europeias e a razão de INJURIES_BACKFILL não ganhar as duas tuplas:
#   2024 (2024/25): events/lineups/stats_fixtures/stats_players/standings/players/predictions=TRUE,
#                   odds=FALSE (encerrada, esperado) e **injuries=FALSE**.
#   2025 (2025/26): idem, mas **injuries=TRUE**.
#   2026 (2026/27): standings/predictions/**odds**=TRUE; events/lineups/stats/players/injuries=FALSE
#                   por pré-temporada (flipam na abertura).
# É o padrão da Libertadores (flag que muda de season p/ season), não o da Ligue 1 (TRUE nas duas).
# ⚠️ ODDS JÁ ESTÁ TRUE NA TEMPORADA CORRENTE — a liga NÃO entra dormente, ao contrário de Ligue 1,
# Bundesliga e Serie A. A abertura é HOJE (2026-08-07 19:15 UTC), então o jogo já está dentro da
# janela de 1-14 dias que faz coverage.odds virar TRUE. O poll captura no 1º ciclo pós-deploy.
# Split-year: season 2026 = 2026/27, start 2026-08-07 -> end 2027-05-16. Rodada 1 espalhada em
# 07/08 (1 jogo), 08/08 (3), 09/08 (4) e 10/08 (1).
# ⚠️ GABARITO DE VERIFICAÇÃO (mesma família de 18 times da Bundesliga/Ligue 1, mas ESTÁVEL):
#   - 306 na temporada nova (18 x 34) — é assim que a liga nova valida;
#   - 308 em 2024/25 E 308 em 2025/26 (306 + playoff de 2 jogos contra a 2ª divisão). Diferente da
#     Ligue 1, onde o bracket variava (308 vs 310) — aqui são 2 jogos nas duas temporadas.
#   - rótulo do playoff INCONSISTENTE, como em todas as outras: "Relegation Round" em 24/25 e
#     "Final" em 25/26. É a API, não o nosso dado.
#   - 19 times em /teams nas duas fechadas (18 + o adversário do playoff), 18 na corrente → o
#     catálogo já traz todos, ZERO risco de órfão em dim_teams.
#   - /standings: 18 linhas por season, grupo único.
#   - NENHUM fixture AWD (≠ Ligue 1, que tem 1 em 25/26): status é FT em 308/308 nas duas.
# ⚠️ AMBIENTE DE GOLS (616 jogos FT, 24/25+25/26): 2,62 gols/jogo e 51,5% de Over 2.5 —
# +0,14 e +4,8pp vs Brasileirão. Desvio BRANDO: fica entre a Serie A ITA (2,49 / 47,0%) e a
# Premier League (2,84 / 55,8%), longe da Bundesliga (3,18 / 61,9%) e da Ligue 1 (2,90 / 54,1%).
# Entra SEM gate e SEM recalibração, como as outras seis.

# Split entre backfill (one-shot, anos anteriores) e current (diário, ano corrente)
LEAGUES_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (COPA_DO_BRASIL_ID, 2024),
    (COPA_DO_BRASIL_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
    (LA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (LA_LIGA_ID, 2025),  # 2025/26
    (PREMIER_LEAGUE_ID, 2024),  # 2024/25 (split-year europeu)
    (PREMIER_LEAGUE_ID, 2025),  # 2025/26
    (UCL_ID, 2024),  # 2024/25 (split-year europeu)
    (UCL_ID, 2025),  # 2025/26
    (SERIE_A_ITA_ID, 2024),  # 2024/25 (split-year europeu)
    (SERIE_A_ITA_ID, 2025),  # 2025/26
    (BUNDESLIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (BUNDESLIGA_ID, 2025),  # 2025/26
    (LIGUE_1_ID, 2024),  # 2024/25 (split-year europeu)
    (LIGUE_1_ID, 2025),  # 2025/26
    (PRIMEIRA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (PRIMEIRA_LIGA_ID, 2025),  # 2025/26
]
LEAGUES_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (COPA_DO_BRASIL_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
    (LA_LIGA_ID, 2026),  # 2026/27 (opener 16/08)
    (PREMIER_LEAGUE_ID, 2026),  # 2026/27 (opener 21/08)
    (UCL_ID, 2026),  # 2026/27, EM CURSO desde 07/07 (fase classificatória)
    (SERIE_A_ITA_ID, 2026),  # 2026/27 (opener 22/08)
    (BUNDESLIGA_ID, 2026),  # 2026/27 (opener 28/08 — a mais tardia do portfólio)
    (LIGUE_1_ID, 2026),  # 2026/27 (opener 21/08)
    (PRIMEIRA_LIGA_ID, 2026),  # 2026/27 (opener 07/08 — HOJE, a mais cedo do portfólio)
]

# Idem leagues — 4 chamadas distribuídas entre backfill (one-shot) e current (diário).
TEAMS_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (COPA_DO_BRASIL_ID, 2024),
    (COPA_DO_BRASIL_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
    (LA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (LA_LIGA_ID, 2025),  # 2025/26
    (PREMIER_LEAGUE_ID, 2024),  # 2024/25 (split-year europeu)
    (PREMIER_LEAGUE_ID, 2025),  # 2025/26
    (UCL_ID, 2024),  # 2024/25 (split-year europeu)
    (UCL_ID, 2025),  # 2025/26
    (SERIE_A_ITA_ID, 2024),  # 2024/25 (split-year europeu)
    (SERIE_A_ITA_ID, 2025),  # 2025/26
    (BUNDESLIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (BUNDESLIGA_ID, 2025),  # 2025/26
    (LIGUE_1_ID, 2024),  # 2024/25 (split-year europeu)
    (LIGUE_1_ID, 2025),  # 2025/26
    (PRIMEIRA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (PRIMEIRA_LIGA_ID, 2025),  # 2025/26
]
TEAMS_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (COPA_DO_BRASIL_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
    (LA_LIGA_ID, 2026),  # 2026/27 (opener 16/08)
    (PREMIER_LEAGUE_ID, 2026),  # 2026/27 (opener 21/08)
    (UCL_ID, 2026),  # 2026/27, EM CURSO desde 07/07
    (SERIE_A_ITA_ID, 2026),  # 2026/27 (opener 22/08)
    (BUNDESLIGA_ID, 2026),  # 2026/27 (opener 28/08 — a mais tardia do portfólio)
    (LIGUE_1_ID, 2026),  # 2026/27 (opener 21/08)
    (PRIMEIRA_LIGA_ID, 2026),  # 2026/27 (opener 07/08 — HOJE, a mais cedo do portfólio)
]

# Idem teams — catálogo de jogadores via /players?league=&season= (paginado).
PLAYERS_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (COPA_DO_BRASIL_ID, 2024),
    (COPA_DO_BRASIL_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
    (LA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (LA_LIGA_ID, 2025),  # 2025/26
    (PREMIER_LEAGUE_ID, 2024),  # 2024/25 (split-year europeu)
    (PREMIER_LEAGUE_ID, 2025),  # 2025/26
    (UCL_ID, 2024),  # 2024/25 (split-year europeu)
    (UCL_ID, 2025),  # 2025/26
    (SERIE_A_ITA_ID, 2024),  # 2024/25 (split-year europeu)
    (SERIE_A_ITA_ID, 2025),  # 2025/26
    (BUNDESLIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (BUNDESLIGA_ID, 2025),  # 2025/26
    (LIGUE_1_ID, 2024),  # 2024/25 (split-year europeu)
    (LIGUE_1_ID, 2025),  # 2025/26
    (PRIMEIRA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (PRIMEIRA_LIGA_ID, 2025),  # 2025/26
]
PLAYERS_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (COPA_DO_BRASIL_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
    (LA_LIGA_ID, 2026),  # 2026/27 (opener 16/08)
    (PREMIER_LEAGUE_ID, 2026),  # 2026/27 (opener 21/08)
    (UCL_ID, 2026),  # 2026/27, EM CURSO desde 07/07
    (SERIE_A_ITA_ID, 2026),  # 2026/27 (opener 22/08)
    (BUNDESLIGA_ID, 2026),  # 2026/27 (opener 28/08 — a mais tardia do portfólio)
    (LIGUE_1_ID, 2026),  # 2026/27 (opener 21/08)
    (PRIMEIRA_LIGA_ID, 2026),  # 2026/27 (opener 07/08 — HOJE, a mais cedo do portfólio)
]

# Fixtures (jogos) — tabela mãe via /fixtures?league=&season= (paginado).
# Cada fixture_id destrava stats/events/lineups/player_stats (subtasks 5-8).
FIXTURES_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (COPA_DO_BRASIL_ID, 2024),
    (COPA_DO_BRASIL_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
    (LA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (LA_LIGA_ID, 2025),  # 2025/26
    (PREMIER_LEAGUE_ID, 2024),  # 2024/25 (split-year europeu)
    (PREMIER_LEAGUE_ID, 2025),  # 2025/26
    (UCL_ID, 2024),  # 2024/25 (split-year europeu)
    (UCL_ID, 2025),  # 2025/26
    (SERIE_A_ITA_ID, 2024),  # 2024/25 (split-year europeu)
    (SERIE_A_ITA_ID, 2025),  # 2025/26
    (BUNDESLIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (BUNDESLIGA_ID, 2025),  # 2025/26
    (LIGUE_1_ID, 2024),  # 2024/25 (split-year europeu)
    (LIGUE_1_ID, 2025),  # 2025/26
    (PRIMEIRA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (PRIMEIRA_LIGA_ID, 2025),  # 2025/26
]
FIXTURES_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (COPA_DO_BRASIL_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
    (LA_LIGA_ID, 2026),  # 2026/27 (opener 16/08)
    (PREMIER_LEAGUE_ID, 2026),  # 2026/27 (opener 21/08, 380 jogos)
    (UCL_ID, 2026),  # 2026/27, EM CURSO desde 07/07 (classificatória, jogos hoje)
    (SERIE_A_ITA_ID, 2026),  # 2026/27 (opener 22/08, 380 jogos)
    (BUNDESLIGA_ID, 2026),  # 2026/27 (opener 28/08 — a mais tardia do portfólio)
    (LIGUE_1_ID, 2026),  # 2026/27 (opener 21/08)
    (PRIMEIRA_LIGA_ID, 2026),  # 2026/27 (opener 07/08 — HOJE, a mais cedo do portfólio)
]

# Standings (/standings) — snapshot diário da tabela do campeonato (1 chamada por
# liga×season, ~20 linhas Brasileirão). Diferente dos demais (latest-only), o arquivo
# é date-stampado (raw_futebol_standings_{mode}_{YYYY-MM-DD}.json): o GCS acumula 1
# snapshot por dia (histórico de evolução da tabela) e re-rodar no mesmo dia
# sobrescreve o mesmo arquivo (idempotente). A API não tem histórico diário — o
# backfill captura a tabela FINAL de 2024/2025 com snapshot_date do dia da coleta.
# ⚠️ Copa do Brasil (73) EXCLUÍDA (backfill E current): mata-mata puro, coverage.standings=FALSE
# (validado 2026-07-10) — o extractor itera SÓ estas tuplas; incluí-la gastaria quota e voltaria vazia.
# Libertadores (13) INCLUÍDA (≠ Copa do Brasil): a fase de grupos tem tabela (coverage.standings=TRUE,
# validado 2026-07-13) e o extractor já achata os N grupos (Group A–H, rank 1-4 por grupo). No
# mata-mata (ago+) a API serve a tabela final dos grupos congelada — snapshot diário segue barato.
# Sudamericana (11) INCLUÍDA (validado 2026-07-14): mesmo formato da 13 — grupos A–H com tabela
# (32 times, rank 1-4/grupo); no mata-mata idem, tabela final dos grupos congelada.
# La Liga (140) INCLUÍDA (probe 2026-07-15): tabela ÚNICA de 20 times (rank 1-20), sem grupos —
# igual Brasileirão/Série B. coverage.standings=TRUE mesmo pré-temporada.
# Premier League (39) INCLUÍDA (probe 2026-07-17): idêntica à La Liga — tabela ÚNICA de 20 times
# (rank 1-20), sem grupos. coverage.standings=TRUE nas 3 seasons, inclusive 2026 pré-temporada.
# UCL (2) INCLUÍDA (probe 2026-07-28): 2024/2025 (encerradas) com standings=TRUE — formato liga
# única de 36 times desde 2024/25. 2026 mostra FALSE por estar em fase classificatória (a tabela
# de 36 times só existe a partir da fase de liga, ~set/2026) — arma dia 0 (custo ~0 até lá),
# mesmo padrão de "armar dormente" já usado em odds da Libertadores/Sudamericana.
# Serie A ITA (135) INCLUÍDA (probe 2026-08-03): idêntica à La Liga/PL — tabela ÚNICA de 20 times
# (rank 1-20), sem grupos. coverage.standings=TRUE nas 3 seasons, inclusive 2026 pré-temporada.
STANDINGS_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (SERIE_B_ID, 2024),
    (SERIE_B_ID, 2025),
    (LIBERTADORES_ID, 2024),
    (LIBERTADORES_ID, 2025),
    (SUDAMERICANA_ID, 2024),
    (SUDAMERICANA_ID, 2025),
    (LA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (LA_LIGA_ID, 2025),  # 2025/26
    (PREMIER_LEAGUE_ID, 2024),  # 2024/25 (split-year europeu)
    (PREMIER_LEAGUE_ID, 2025),  # 2025/26
    (UCL_ID, 2024),  # 2024/25 (split-year europeu)
    (UCL_ID, 2025),  # 2025/26
    (SERIE_A_ITA_ID, 2024),  # 2024/25 (split-year europeu)
    (SERIE_A_ITA_ID, 2025),  # 2025/26
    (BUNDESLIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (BUNDESLIGA_ID, 2025),  # 2025/26
    (LIGUE_1_ID, 2024),  # 2024/25 (split-year europeu)
    (LIGUE_1_ID, 2025),  # 2025/26
    (PRIMEIRA_LIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (PRIMEIRA_LIGA_ID, 2025),  # 2025/26
]
STANDINGS_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (COPA_MUNDO_ID, 2026),
    (SERIE_B_ID, 2026),
    (LIBERTADORES_ID, 2026),
    (SUDAMERICANA_ID, 2026),
    (LA_LIGA_ID, 2026),  # 2026/27 (opener 16/08)
    (PREMIER_LEAGUE_ID, 2026),  # 2026/27 (opener 21/08)
    (UCL_ID, 2026),  # 2026/27, FALSE até a fase de liga começar (~set/2026); armada dia 0
    (SERIE_A_ITA_ID, 2026),  # 2026/27 (opener 22/08) — standings=TRUE já pré-temporada
    (BUNDESLIGA_ID, 2026),  # 2026/27 (opener 28/08) — standings=TRUE já pré-temporada
    (LIGUE_1_ID, 2026),  # 2026/27 (opener 21/08) — standings=TRUE já pré-temporada
    (PRIMEIRA_LIGA_ID, 2026),  # 2026/27 (opener 07/08 — HOJE) — standings=TRUE já pré-temporada
]

# Injuries (/injuries) — snapshot diário de lesionados/suspensos (1 chamada/liga×season,
# NÃO paginada — como /fixtures, rejeita `page` e devolve o log de lesões da temporada
# INTEIRA, ~2k linhas/liga). Mesma mecânica date-stampada do standings
# (raw_futebol_injuries_{mode}_{data}.json): o GCS acumula 1 snapshot/dia e re-rodar no
# mesmo dia sobrescreve (idempotente). ⚠️ A API repete linhas EXATAS — dedup no fato.
# ⚠️ Coverage validado em dim_leagues (2026-06-15): coverage.injuries = TRUE só p/
# Brasileirão (71) 2024/25/26. Copa do Mundo (1) 2026 = FALSE → EXCLUÍDA (a API não
# fornece lesões da Copa; incluí-la gastaria quota e voltaria vazia). Série B (72)
# também EXCLUÍDA: coverage.injuries=FALSE em 2024/25/26 (validado 2026-07).
# Copa do Brasil (73) idem: coverage.injuries=FALSE em 2024/25/26 (validado 2026-07-10).
# Libertadores (13): coverage.injuries=FALSE em 2026 (e 2024), mas 2025 FOI TRUE — RECHECAR em
# agosto via dim_leagues antes de decidir; se virar TRUE: 13 em INJURIES_CURRENT +
# FUTEBOL_INJURIES_LEAGUE_IDS + deploy cirúrgico extract-injuries (validado 2026-07-13).
# Sudamericana (11): coverage.injuries=FALSE em 2024/25/26 (validado 2026-07-14) — exclusão
# simples, SEM recheck de agosto (≠ Liberta).
# La Liga (140): coverage.injuries=TRUE em 2024 E 2025 (probe 2026-07-15) — 1ª liga da expansão
# com injuries LIGADO (season-log + pregame). 2026 mostra FALSE só por pré-temporada; flipa ao começar.
# Premier League (39): coverage.injuries=TRUE em 2024 E 2025 (probe 2026-07-17) — mesma leitura da
# La Liga, 2026 FALSE só por pré-temporada (abertura 21/08).
# UCL (2): coverage.injuries=TRUE em 2024 E 2025 (probe 2026-07-28, temporadas encerradas) — só
# backfill por ora. 2026 (EM CURSO desde 07/07) mostra FALSE — mesmo caveat da Libertadores
# (13): NÃO entra em INJURIES_CURRENT/FUTEBOL_INJURIES_LEAGUE_IDS ainda; RECHECAR quando a fase
# de liga (36 times) começar (~set/2026) via dim_leagues antes de ligar (senão chamada
# desperdiçada + tabela vazia).
# Serie A ITA (135): coverage.injuries=TRUE em 2024 E 2025 (probe 2026-08-03) — mesma leitura de
# La Liga/PL, 2026 FALSE só por pré-temporada (abertura 22/08). 4ª liga com injuries LIGADO
# (season-log + pregame).
# Bundesliga (78): coverage.injuries=TRUE em 2024 E 2025 (probe 2026-08-05) — mesma leitura de
# La Liga/PL/Serie A, 2026 FALSE só por pré-temporada (abertura 28/08). 5ª liga com injuries
# LIGADO (season-log + pregame).
# Ligue 1 (61): coverage.injuries=TRUE em 2024 E 2025 (probe 2026-08-06) — mesma leitura de
# La Liga/PL/Serie A/Bundesliga, 2026 FALSE só por pré-temporada (abertura 21/08). 6ª e última
# liga com injuries LIGADO (season-log + pregame).
INJURIES_BACKFILL = [
    (BRASILEIRAO_ID, 2024),
    (BRASILEIRAO_ID, 2025),
    (LA_LIGA_ID, 2024),  # La Liga: coverage.injuries=TRUE em 2024 (probe 2026-07-15) — 1ª além do Brasileirão
    (LA_LIGA_ID, 2025),  # coverage.injuries=TRUE em 2025 (probe)
    (PREMIER_LEAGUE_ID, 2024),  # Premier League: coverage.injuries=TRUE em 2024 (probe 2026-07-17)
    (PREMIER_LEAGUE_ID, 2025),  # coverage.injuries=TRUE em 2025 (probe)
    (UCL_ID, 2024),  # UCL: coverage.injuries=TRUE em 2024 (probe 2026-07-28)
    (UCL_ID, 2025),  # coverage.injuries=TRUE em 2025 (probe)
    (SERIE_A_ITA_ID, 2024),  # Serie A ITA: coverage.injuries=TRUE em 2024 (probe 2026-08-03)
    (SERIE_A_ITA_ID, 2025),  # coverage.injuries=TRUE em 2025 (probe)
    (BUNDESLIGA_ID, 2024),  # 2024/25 (split-year europeu)
    (BUNDESLIGA_ID, 2025),  # 2025/26
    (LIGUE_1_ID, 2024),  # 2024/25 (split-year europeu)
    (LIGUE_1_ID, 2025),  # 2025/26
    (PRIMEIRA_LIGA_ID, 2025),  # 2025/26 — SÓ esta: injuries=FALSE em 2024 (probe), tupla de 2024 seria chamada vazia
]
INJURIES_CURRENT = [
    (BRASILEIRAO_ID, 2026),
    (LA_LIGA_ID, 2026),  # season-log 2026/27: injuries=FALSE pré-temporada (probe), flipa ao começar; iterar já é barato (0 linhas até lá)
    (PREMIER_LEAGUE_ID, 2026),  # idem La Liga: FALSE pré-temporada (probe), flipa em 21/08; 0 linhas até lá
    (SERIE_A_ITA_ID, 2026),  # idem La Liga/PL: FALSE pré-temporada (probe), flipa em 22/08; 0 linhas até lá
    (BUNDESLIGA_ID, 2026),  # idem: FALSE pré-temporada (probe), flipa em 28/08; 0 linhas até lá
    (LIGUE_1_ID, 2026),  # idem La Liga/PL/Serie A/Bundesliga: FALSE pré-temporada (probe), flipa em 21/08; 0 linhas até lá
    (PRIMEIRA_LIGA_ID, 2026),  # FALSE pré-temporada mas 2025 foi TRUE (probe) → flipa na abertura de hoje; 0 linhas até lá
    # UCL (2026) NÃO incluída ainda — coverage.injuries=FALSE na fase classificatória (probe
    # 2026-07-28); recheck quando a fase de liga começar (~set/2026), igual ao caveat da 13.
]

# Odds (/odds) — coração do value betting. Snapshot pré-jogo de TODAS as casas em 2
# janelas por jogo (T-24h = linha de abertura; T-1h = linha perto do fechamento) p/
# permitir CLV/EV e movimento de linha. Coleta FORWARD-ONLY (não dá pra reconstruir as
# janelas de jogos passados) via poll ~15min (workflow_futebol_odds.yml), espelhando o
# poll pré-jogo das escalações. 1 chamada por (fixture, janela); 1 arquivo por (fixture,
# janela): raw_futebol_odds_{fixture}_{t24h|t1h|t15m}.json (sufixo de fase no get_gcs_path).
#
# FUTEBOL_ODDS_WINDOWS: banda (lead_min, lead_max) em MINUTOS até o kickoff. A 1ª passada
# do poll com lead na banda captura; skip-if-exists trava as seguintes (1 captura/janela).
# Banda > intervalo de poll p/ não furar; a captura cai perto de lead_max (lead decresce
# no tempo). minutes_to_kickoff exato é registrado no fato — a precisão de CLV não depende
# da largura da banda. t15m (0,15) é a LINHA DE FECHAMENTO (CLV real): banda inclusiva [0,15]
# + poll */15 garante 1 captura perto do kickoff (lead≈15→0); forward-only, cada dia sem
# t15m = linha de fechamento perdida pra sempre.
#
# HORIZONTE (desde 2026-08-07): 7 dias. O board era, por construção, um board de 24 horas —
# quem abria de manhã via os jogos de hoje à noite e mais nada. O corte era NOSSO, não da
# fonte: sondagem direta em 2026-08-05 devolveu 12–13 casas, Pinnacle inclusa, a 51h, 72h,
# 77h, 96h e 147h do apito. A janela "daily" cobre de pouco além de 24h até o horizonte, com
# 1 captura por fixture por dia (date-stampada, mesmo arquétipo do predictions). Ampliar de 7
# para N dias é mudar ESTE número; as bandas de fechamento não se mexem.
#
# ⚠️ DISJUNÇÃO É REQUISITO. O piso da "daily" começa 1 minuto acima do teto da t24h. Bandas
# sobrepostas fazem a MESMA passada do poll bucketar o mesmo fixture em duas janelas: duas
# chamadas, dois arquivos, duas linhas no fato com rótulos diferentes p/ o mesmo preço. O
# teste em tests/test_odds_pregame.py trava qualquer sobreposição futura.
FUTEBOL_ODDS_HORIZON_MIN = 7 * 24 * 60  # 7 dias
FUTEBOL_ODDS_WINDOWS = {
    "daily": (1441, FUTEBOL_ODDS_HORIZON_MIN),  # >24h até o horizonte — 1 captura/dia
    "t24h": (1320, 1440),  # 22h–24h antes (alvo 24h — linha de abertura)
    "t1h":  (30, 60),      # 30–60min antes (alvo 1h — linha intermediária)
    "t15m": (0, 15),       # 0–15min antes (alvo ~15min — linha de fechamento p/ CLV real)
}

# Janelas de odds que DATE-STAMPAM o arquivo (raw_futebol_odds_{fixture}_{janela}_{data}.json)
# → skip-if-exists por (fixture, janela, DIA), ou seja 1 captura/dia enquanto o fixture ficar
# na banda. Só a "daily": as bandas de fechamento são 1 captura única por fixture e o nome sem
# data é o que a external table e o fato já leem — date-stampá-las seria regressão.
FUTEBOL_ODDS_WINDOWS_DIARIAS = {"daily"}

# Ligas com coverage.odds=TRUE (validado em dim_leagues). O poll filtra os jogos NS
# por esses league_ids. Diferente de /injuries (Copa excluída), odds de Copa do Mundo
# normalmente existem — manter 1 aqui se a validação confirmar coverage.odds=TRUE.
# Copa do Brasil (73)/Libertadores (13) ARMADAS 2026-07-13, Sudamericana (11) 2026-07-14, La Liga (140) 2026-07-15 (dormente até t24h ~15/08, opener 16/08),
# Premier League (39) 2026-07-17 (dormente até t24h ~20/08, opener 21/08 19:00 UTC)
# (mesma decisão: armar no dia 0, sem deploy futuro p/ não esquecer). ⚠️ DORMENTE DEIXOU DE SER
# CUSTO ZERO em 2026-08-07, com a janela diária: o poll passa a chamar /odds p/ NS com lead até 7
# dias, e liga em pré-temporada (coverage.odds=FALSE) devolve vazio. O custo é limitado a 1
# chamada por fixture por dia — e só porque a diária GRAVA o vazio registrado; sem isso o
# skip-if-exists nunca travaria e seriam ~96/dia por fixture (poll de 15min × 6 dias de banda).
# Até o t24h abrir: Sudamericana ~20/07
# (R32/repechagem ida 21–24/07, volta 28–31/07; oitavas ida 11–13/08, volta 18–20/08),
# CdB ~31/07 (oitavas 01/08), Libertadores 10/08 (ida 11/08, volta 18/08). coverage.odds é
# sazonal (FALSE fora da janela 1-14d); a checagem de Pinnacle (id=4) virou verificação
# pós-coleta: sem Pinnacle o board não abre 1X2/AH/OU (de-vig ancora nele; consenso só
# BTTS/HT-FT/DC) → feed ruim = board fechado, nunca sinal lixo. ⚠️ RENOVAR O PLANO PRO ATÉ
# 10/08 (expira 11/08 12:21 UTC): sem renovar, t1h/t15m de 11/08, as voltas de 18–20/08 e
# TODO o pipeline diário param — na 11, até o t24h das oitavas de 12–13/08 cai APÓS a expiração.
# UCL (2) — início Onda 2 — ARMADA 2026-07-28: DIFERENTE das demais desta lista, já está
# ATIVA agora (coverage.odds=TRUE, fase classificatória em curso desde 07/07; confirmado
# 13 casas incl. Pinnacle em jogos de hoje 28/07). Não é dormente — o poll já vai capturar
# t24h/t1h/t15m dos próximos jogos classificatórios a partir do próximo ciclo pós-deploy.
# Serie A ITA (2) ARMADA 2026-08-03: volta ao padrão dormente (≠ UCL) — coverage.odds=FALSE agora
# (pré-temporada, sazonal) e o t24h só abre ~21/08 (opener 22/08 16:30 UTC). Custo 0 até lá.
# Bundesliga (78) ARMADA 2026-08-05: dormente também, e a mais longa de todas — coverage.odds=FALSE
# agora e o t24h só abre ~27/08 (opener 28/08, a abertura mais tardia do portfólio). ~3 semanas de
# custo 0. Armar no dia 0 é justamente p/ não depender de um deploy futuro em 27/08.
# Ligue 1 (61) ARMADA 2026-08-06: dormente igual, coverage.odds=FALSE agora e o t24h abre ~20/08
# (opener 21/08 18:45 UTC — a task do ClickUp diz 23/08 e está errada; 23/08 é o fim da rodada 1,
# e planejar por ela perderia a captura dos 3 primeiros jogos). ~2 semanas de custo 0.
FUTEBOL_ODDS_LEAGUE_IDS = [BRASILEIRAO_ID, COPA_MUNDO_ID, SERIE_B_ID, COPA_DO_BRASIL_ID, LIBERTADORES_ID, SUDAMERICANA_ID, LA_LIGA_ID, PREMIER_LEAGUE_ID, UCL_ID, SERIE_A_ITA_ID, BUNDESLIGA_ID, LIGUE_1_ID, PRIMEIRA_LIGA_ID]

# Predictions (/predictions) — BASELINE de comparação (a previsão do algoritmo da própria
# API) E fonte da corroboração `modelo_api_concorda` (+7) do Motor de Score. Não é produto:
# serve p/ avaliar se um modelo nosso bate a API consistentemente (= edge real). FORWARD-ONLY
# (previsão de jogo passado não é reconstruível — a API recomputa com o resultado conhecido),
# poll ~15min dedicado (workflow_futebol_predictions.yml).
#
# DUAS janelas (a API atualiza ~1x/h):
#   - "daily": varre TODO jogo NS de ~2h até 14 dias à frente → garante que jogos futuros
#     tenham previsão (sem isso, `modelo_api_concorda` quase nunca dispara). Recaptura 1x/dia
#     porque o path é date-stampado (raw_futebol_predictions_{fixture}_daily_{YYYY-MM-DD}.json)
#     → skip-if-exists é por (fixture, janela, DIA). A 1ª passada do poll no dia captura; as
#     demais pulam. Bandas DISJUNTAS de t2h (≥131 vs ≤130) p/ não capturar 2x no mesmo poll.
#   - "t2h": refresh perto do kickoff (linha mais fresca p/ o jogo do dia).
# O fato dedup latest-wins por loaded_at → o snapshot mais fresco (t2h no dia, ou o diário
# mais recente) vence; os snapshots acumulam no GCS (padrão de standings/injuries).
#
# FUTEBOL_PREDICTIONS_WINDOWS: banda (lead_min, lead_max) em MINUTOS até o kickoff (mesma
# mecânica de FUTEBOL_ODDS_WINDOWS). Banda > intervalo de poll p/ não furar. Horizonte de 14d
# alinha com a janela de odds (1–14d): a previsão só corrobora quando há odds. Tunável.
FUTEBOL_PREDICTIONS_WINDOWS = {
    "daily": (131, 20160),  # ~2h até 14 dias — varre todo NS futuro; 1 captura/dia (date-stamp)
    "t2h":   (100, 130),    # 100–130min antes (refresh perto do jogo; captura cai perto de 130)
}

# coverage.predictions=TRUE p/ Brasileirão (71) E Copa do Mundo (1) — validado em
# dim_leagues (2026-06-17). Diferente de /injuries (Copa excluída): ambos incluídos.
# Copa do Brasil (73): predictions TRUE e REAIS já nas oitavas (validado 2026-07-10,
# ex.: 35/35/30 + advice — não é o placeholder 45/45/10 de mata-mata da Copa do Mundo).
# Libertadores (13): coverage.predictions=TRUE (validado 2026-07-13). A janela daily de 14d
# só alcança as oitavas ~28/07 (ida 11/08) — 0 linhas até lá é esperado; na 1ª captura
# conferir REAL (padrão CdB) vs placeholder 45/45/10 (padrão Copa do Mundo, viraria ruído
# no modelo_api_concorda).
# Sudamericana (11): coverage.predictions=TRUE (validado 2026-07-14). A R32 (16 NS em 21–31/07)
# JÁ está na janela de 14d → 1ª captura no 1º ciclo de poll pós-deploy; mesma conferência
# REAL vs placeholder da 13 (mata-mata CONMEBOL), só que imediata.
# Premier League (39): coverage.predictions=TRUE nas 3 seasons (probe 2026-07-17), inclusive 2026
# pré-temporada. A janela daily de 14d só alcança o opener (21/08) ~07/08 — 0 linhas até lá é
# esperado. Liga de pontos corridos com histórico cheio → esperado REAL (padrão La Liga/Brasileirão),
# não o placeholder 45/45/10 de mata-mata da Copa do Mundo.
# UCL (2): coverage.predictions=TRUE (probe 2026-07-28) — JÁ ATIVA (fase classificatória em curso
# desde 07/07); ao contrário de PL/La Liga, a janela daily de 14d já alcança jogos reais hoje.
# Mata-mata (repescagem/playoff) — conferir REAL vs placeholder 45/45/10 na 1ª captura pós-deploy
# (mesmo padrão de checagem já usado em Libertadores/Sudamericana/Copa do Brasil).
# Serie A ITA (135): coverage.predictions=TRUE nas 3 seasons (probe 2026-08-03), inclusive 2026
# pré-temporada. A janela daily de 14d só alcança o opener (22/08) ~08/08 — 0 linhas até lá é
# esperado. Liga de pontos corridos com histórico cheio → esperado REAL (padrão La Liga/PL).
# Bundesliga (78): coverage.predictions=TRUE nas 3 seasons (probe 2026-08-05), inclusive 2026
# pré-temporada. A janela daily de 14d só alcança o opener (28/08) ~14/08 — 0 linhas até lá é
# esperado. Mesma leitura da Serie A/La Liga/PL → esperado REAL, não placeholder 45/45/10.
# Ligue 1 (61): coverage.predictions=TRUE nas 3 seasons (probe 2026-08-06), inclusive 2026
# pré-temporada. A janela daily de 14d alcança o opener (21/08) já em ~07/08, ou seja QUASE
# IMEDIATAMENTE depois do deploy — ≠ Bundesliga, que espera até 14/08. Liga de pontos corridos
# com histórico cheio → esperado REAL, não placeholder 45/45/10.
FUTEBOL_PREDICTIONS_LEAGUE_IDS = [BRASILEIRAO_ID, COPA_MUNDO_ID, SERIE_B_ID, COPA_DO_BRASIL_ID, LIBERTADORES_ID, SUDAMERICANA_ID, LA_LIGA_ID, PREMIER_LEAGUE_ID, UCL_ID, SERIE_A_ITA_ID, BUNDESLIGA_ID, LIGUE_1_ID, PRIMEIRA_LIGA_ID]

# Injuries PRÉ-JOGO (/injuries?fixture) — coleta FORWARD-ONLY por fixture (modo "pregame"),
# complementando o snapshot season-log diário (INJURIES_CURRENT, /injuries?league&season).
# Motivo (S7 do Motor de Score): o season-log fica congelado quando não há rodada recente
# (ex.: pausa FIFA) — não traz desfalques dos JOGOS FUTUROS. O endpoint por fixture devolve
# os lesionados/suspensos ligados àquele jogo específico, então varremos os NS futuros.
#
# Mesma mecânica date-stampada de predictions (raw_futebol_injuries_{fixture}_daily_{data}.json):
# skip-if-exists por (fixture, janela, DIA) → 1 captura/dia; o fato dedup latest-wins por
# loaded_at. Janela ÚNICA "daily", de agora (0) até o horizonte. Por que SÓ "daily" (sem banda
# near-kickoff): injuries são estáveis intra-dia (jogador descartado de manhã segue fora) E o
# fact_injuries_snapshot é daily-grained (dedup por snapshot_date) → re-poll horário não
# adiciona resolução; a notícia final de escalação vem da fonte de lineups (confirmed, ~T-30min).
#
# HORIZONTE: 96h (era 14 dias até 2026-08-06). A fonte só publica a lista a 53–70h do apito
# — mediana 58h, medido sobre os 28 fixtures que já tiveram lista, coletados entre 14 e 31/07,
# TODOS do Brasileirão. Os primeiros 11 dias e meio da banda antiga nunca devolviam nada, e o
# poll horário repergunta o mesmo vazio até o kickoff: ~648 chamadas/dia, 8,6% da cota, para
# reconfirmar de hora em hora que a fonte ainda não publicou.
# 96h e não 72h justamente porque a amostra é pequena e de uma liga só: dá folga sobre o limite
# superior observado. ⚠️ RECHECAR com amostra europeia — se La Liga/PL/Serie A/Bundesliga/Ligue 1
# publicarem com antecedência diferente, a banda merece revisão. Com a sentinela do vazio
# registrado (#33) gravando, passa a existir dado para revisá-la: hoje "perguntamos e não tinha"
# não deixa rastro nenhum.
# Mudar o horizonte é mudar ESTE número — a banda é derivada, não uma segunda entrada no mapa.
# Em MINUTOS, como todas as bandas do projeto (FUTEBOL_ODDS_WINDOWS/FUTEBOL_PREDICTIONS_WINDOWS):
# uma unidade só evita `* 60` espalhado por config e testes.
FUTEBOL_INJURIES_HORIZON_MIN = 96 * 60
FUTEBOL_INJURIES_WINDOWS = {
    "daily": (0, FUTEBOL_INJURIES_HORIZON_MIN),  # 0 até o horizonte; 1 captura/dia
}

# coverage.injuries=TRUE p/ Brasileirão (71), La Liga (140, probe 2026-07-15), Premier League
# (39, probe 2026-07-17) e Serie A ITA (135, probe 2026-08-03); Copa do Mundo (1) EXCLUÍDA (igual a
# INJURIES_CURRENT — a API não fornece lesões da Copa; incluí-la gastaria quota e voltaria vazia).
# Série B (72) também EXCLUÍDA: coverage.injuries=FALSE (validado 2026-07). As europeias varrem
# NS dentro do horizonte de 96h (pregame) e ficam DORMENTES, a custo zero, até 4 dias antes do
# opener de cada uma — datas recalculadas em 2026-08-06, quando o horizonte caiu de 14 dias
# para 96h: La Liga ~12/08 (opener 16/08), Premier League ~17/08 (21/08), Ligue 1 ~17/08 (21/08),
# Serie A ITA ~18/08 (22/08), Bundesliga ~24/08 (28/08). Antes engajavam ~10 dias mais cedo, e
# cada um desses dias era varredura vazia paga de hora em hora.
FUTEBOL_INJURIES_LEAGUE_IDS = [BRASILEIRAO_ID, LA_LIGA_ID, PREMIER_LEAGUE_ID, SERIE_A_ITA_ID, BUNDESLIGA_ID, LIGUE_1_ID, PRIMEIRA_LIGA_ID]

# Fixture statistics (/fixtures/statistics) — 1 chamada por fixture, só após FT.
# No modo current, re-busca jogos cujo kickoff foi nos últimos N dias (captura
# correções pós-jogo da API); jogos mais antigos já salvos são pulados (skip-if-exists).
FIXTURE_STATS_REFETCH_WINDOW_DAYS = 3

# Fixture events (/fixtures/events) — 1 chamada por fixture, só após FT. Mesma
# mecânica do statistics: no modo current re-busca jogos dos últimos N dias
# (captura VAR/correções pós-jogo da API), o resto é pulado por skip-if-exists.
FIXTURE_EVENTS_REFETCH_WINDOW_DAYS = 3

# Fixture lineups (/fixtures/lineups) — 1 chamada por fixture, em duas fases:
# - pós-jogo (mode current/backfill): escalação real (lineup_phase="real"), mesma
#   mecânica do statistics/events (janela 3d no current + skip-if-exists).
# - pré-jogo (mode pregame): escalação confirmada (lineup_phase="confirmed") dos jogos
#   NS com kickoff nos próximos FIXTURE_LINEUPS_PREGAME_WINDOW_MIN minutos. O fato é
#   latest-wins (dbt) — "real" vence "confirmed"; o GCS guarda os dois snapshots.
FIXTURE_LINEUPS_REFETCH_WINDOW_DAYS = 3
FIXTURE_LINEUPS_PREGAME_WINDOW_MIN = 45

# Fixture player stats (/fixtures/players) — 1 chamada por fixture, só após FT.
# Mesma mecânica do statistics/events: no modo current re-busca jogos dos últimos N
# dias (captura correções pós-jogo da API, ex.: rating revisado), o resto é pulado
# por skip-if-exists.
FIXTURE_PLAYER_STATS_REFETCH_WINDOW_DAYS = 3

# GCS Configuration
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "smartbetting-landing")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCS_USE_ADC = True  # Application Default Credentials

# BigQuery Configuration
BIGQUERY_PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID", "smartbetting-dados")
BIGQUERY_DATASET = os.getenv("BIGQUERY_DATASET", "nba")
BIGQUERY_DATASET_FUTEBOL = os.getenv("BIGQUERY_DATASET_FUTEBOL", "futebol")
BIGQUERY_LOCATION = os.getenv("BIGQUERY_LOCATION", "us-east1")
BIGQUERY_DATASET_SANDBOX = os.getenv("BIGQUERY_DATASET_SANDBOX", "sandbox")

# Supabase Postgres sync configuration
# Connection strings DEVEM usar porta 5432 (sessão direta), NÃO 6543 (pgbouncer):
# pgbouncer em modo transaction não suporta COPY nem prepared statements.
# Dois ambientes: PRD recebe sync agendado via workflow, DEV idem (sequencial).
SUPABASE_PG_URL_PRD = os.getenv("SUPABASE_PG_URL_PRD")
SUPABASE_PG_URL_DEV = os.getenv("SUPABASE_PG_URL_DEV")
MART_PG_SCHEMA = "nba_mart"
# Futebol grava no schema nativo `futebol` (mesmas RPCs get_futebol_* já leem ele).
# Mesmo Supabase PRD/DEV do NBA — só muda dataset BQ de origem e schema destino.
FUTEBOL_MART_PG_SCHEMA = "futebol"


def get_pg_url(env: str) -> str:
    """Resolve URL Postgres por ambiente. Levanta se não configurado."""
    env = (env or "prd").lower()
    if env == "prd":
        url = SUPABASE_PG_URL_PRD
    elif env == "dev":
        url = SUPABASE_PG_URL_DEV
    else:
        raise ValueError(f"env inválido: {env}. Use 'prd' ou 'dev'.")
    if not url:
        raise RuntimeError(
            f"SUPABASE_PG_URL_{env.upper()} não configurado. "
            f"Settar env var (porta 5432, NÃO 6543 pgbouncer)."
        )
    return url

# Ordem deliberada: dimensões primeiro, depois fatos, depois marts derivadas.
# Reduz janela de inconsistência cross-table durante o sync.
# As 6 tabelas de passing / contexto de time / período foram adicionadas em
# 2026-06: tinham ficado de fora do cutover FDW->sync (só 9 dos 15 marts BI
# foram portados). As tabelas destino são criadas pela migration 069 do
# prop-play-predictor (nba_mart). check_schema_parity já validado contra o BQ.
MART_TABLES_ORDERED = [
    # dimensões base
    "dim_teams",
    "dim_players",
    "dim_stat_player",
    "dim_player_shooting_by_zones",
    "dim_player_passing_stats",
    "dim_player_latest_line",
    "dim_team_opponent_stats",
    "dim_team_playtypes",
    "dim_team_shooting_zone_defense",
    # fatos
    "ft_games",
    "ft_game_player_stats",
    "ft_game_player_passing_stats",
    "ft_game_player_stats_period",
    # marts derivadas
    "dim_teammate_impact_360",
    "dim_daily_opportunities",
]

# Futebol — as 21 tabelas que o app (RPCs get_futebol_*) consome, na ordem
# dim -> fact -> intermediário -> mart-produto (mesma lógica de janela mínima de
# inconsistência do NBA). NÃO é só "marts": as RPCs de valor reconstroem
# evidências/avisos a partir dos booleans das int_futebol_premissas_*, então elas
# entram no sync. Espelha o array de futebol.sync_all() que o FDW+pg_cron rodava.
# Colunas BQ complexas (coverage RECORD, evidencias/avisos ARRAY) são puladas pelo
# engine (Postgres nativo é escalar) — ver _is_complex_field em sync/bq_to_postgres.py.
FUTEBOL_SYNC_TABLES_ORDERED = [
    # dimensões
    "dim_leagues",
    "dim_teams",
    # fatos base
    "fact_fixtures",
    "fact_fixture_stats",
    "fact_fixture_events",
    "fact_fixture_lineups",
    "fact_fixture_lineups_players",
    "fact_fixture_player_stats",
    "fact_h2h",
    "fact_injuries_snapshot",
    "fact_standings_snapshot",
    "fact_team_season_stats",
    "fact_odds_snapshot",
    "fact_predictions_api",
    # camada de valor (intermediários -> mart-produto por último)
    "int_futebol_odds_devig",
    "int_futebol_premissas_1x2",
    "int_futebol_premissas_ou",
    "int_futebol_premissas_ah",
    "int_futebol_premissas_btts",
    "int_futebol_premissas_dc",
    "fact_value_opportunities",
    "fact_value_opportunities_hist",
]


def get_sync_target(sport: str = "nba") -> tuple:
    """Resolve (dataset BQ, schema Postgres, tabelas ordenadas) por esporte.

    O engine de sync (sync/bq_to_postgres.py) é sport-agnostic; cada esporte amarra
    um dataset BQ a um schema Postgres + a allowlist ordenada (dim -> fact -> mart).
    Mantém o NBA como default → comportamento idêntico ao anterior.
    """
    sport = (sport or "nba").lower()
    if sport == "nba":
        return BIGQUERY_DATASET, MART_PG_SCHEMA, list(MART_TABLES_ORDERED)
    if sport == "futebol":
        return BIGQUERY_DATASET_FUTEBOL, FUTEBOL_MART_PG_SCHEMA, list(FUTEBOL_SYNC_TABLES_ORDERED)
    raise ValueError(f"sport inválido: {sport!r}. Use 'nba' ou 'futebol'.")

# Season Configuration
try:
    SEASON = int(os.getenv("SEASON", "2025"))
except (TypeError, ValueError) as e:
    raise RuntimeError(f"SEASON inválido: {os.getenv('SEASON')!r} (esperado inteiro)") from e

# Temporadas a extrair (backfill + corrente)
SEASONS = [2023, 2024, 2025]

# Data de fim para temporadas já encerradas (corrente usa datetime.now())
NBA_SEASON_END_DATES = {
    2023: "2024-06-17",  # Finals 2023-24: Boston x Dallas
    2024: "2025-06-22",  # Finals 2024-25 (estimado)
}

# Janelas play-in e playoffs por temporada
NBA_SEASON_TYPE_DATES = {
    2023: {"playin_start": "2024-04-16", "playoffs_start": "2024-04-20"},
    2024: {"playin_start": "2025-04-15", "playoffs_start": "2025-04-19"},
    2025: {"playin_start": "2026-04-14", "playoffs_start": "2026-04-18"},
}


def get_season_type_for_date(game_date: str, season: int) -> str:
    """Retorna 'regular', 'playin' ou 'playoffs' com base na data do jogo."""
    dates = NBA_SEASON_TYPE_DATES.get(season, {})
    if not dates:
        return "regular"
    if game_date >= dates.get("playoffs_start", "9999"):
        return "playoffs"
    if game_date >= dates.get("playin_start", "9999"):
        return "playin"
    return "regular"

# Per-game NBA (betting_odds / player_props): skip-if-exists + janela de re-fetch.
# Mesma mecânica dos per-fixture de futebol — odds/props de jogos antigos não mudam,
# então pula jogos cujo arquivo já existe no GCS, exceto os dos últimos N dias (re-busca
# correções/linhas que ainda estavam abrindo). Evita reprocessar milhares de jogos a cada
# run (quota/tempo) perto do fim da temporada. NBA não expõe data por game_id facilmente
# aqui, então a janela é aplicada por skip-if-exists puro (re-fetch só quando ainda não há
# arquivo). PER_GAME_SLEEP_SECONDS é a cortesia entre chamadas (rate-limit), adicionada
# JUNTO do skip-if-exists para não agravar o timeout reprocessando tudo.
NBA_PER_GAME_REFETCH_WINDOW_DAYS = 2
NBA_PER_GAME_SLEEP_SECONDS = 0.2

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Endpoint-specific configurations
ENDPOINT_CONFIGS = {
    "games": {
        "has_date": True,
        "per_page": 100,
    },
    "game_player_stats": {
        "has_date": True,
        "per_page": 100,
    },
    "season_averages": {
        "has_date": False,
        "per_page": 100,
    },
    "team_season_averages": {
        "has_date": False,
        "per_page": 100,
    },
    "active_players": {
        "has_date": False,
        "per_page": 100,
    },
    "player_injuries": {
        "has_date": False,
        "per_page": 100,
    },
    "team_standings": {
        "has_date": False,
        "per_page": 100,
    },
    "player_props": {
        "has_date": True,
        "per_page": 100,
        "market": "draftkings",
        "vendors": [
            "draftkings",
            "caesars",
            "betrivers",
        ],
    },
    "betting_odds": {
        "has_date": False,
        "per_page": 100,
    },
    "game_player_stats_period": {
        "has_date": True,
        "per_page": 100,
        "periods": [1, 2, 3, 4],
    },
    "game_player_advanced_stats": {
        "has_date": True,
        "per_page": 100,
    },
}

# Combinações de season_averages / team_season_averages — FONTE ÚNICA.
# Tanto os scripts de extração (scripts/extract_season_averages.py,
# scripts/extract_team_season_averages.py) quanto a criação das external tables
# (src/bigquery/bigquery_client.py) importam estas listas daqui. Antes estavam
# duplicadas em ambos os lados, exigindo sincronização manual (um novo type só num
# lugar gerava tabela apontando p/ arquivo inexistente ou vice-versa).
SEASON_AVERAGES_COMBINATIONS = [
    {"category": "general", "type": "base"},
    {"category": "general", "type": "advanced"},
    {"category": "shooting", "type": "by_zone"},
    {"category": "tracking", "type": "passing"},
]

TEAM_SEASON_AVERAGES_COMBINATIONS = [
    {"category": "general",   "type": "advanced"},
    {"category": "general",   "type": "opponent"},
    {"category": "general",   "type": "defense"},
    {"category": "tracking",  "type": "rebounding"},
    # Opp Rankings — Play Types
    {"category": "playtype",     "type": "isolation"},
    {"category": "playtype",     "type": "transition"},
    {"category": "playtype",     "type": "spotup"},
    {"category": "playtype",     "type": "handoff"},
    {"category": "playtype",     "type": "cut"},
    {"category": "playtype",     "type": "offscreen"},
    {"category": "playtype",     "type": "postup"},
    {"category": "playtype",     "type": "prballhandler"},
    {"category": "playtype",     "type": "prrollman"},
    {"category": "playtype",     "type": "offrebound"},
    # Opp Rankings — Hustle / Rim Protection
    {"category": "hustle",       "type": "overall"},
    {"category": "tracking",     "type": "defense"},
    # Opp Rankings — C&S / Pull Up (shotdashboard)
    {"category": "shotdashboard", "type": "catch_and_shoot"},
    {"category": "shotdashboard", "type": "pullups"},
    # Opp Rankings — Shooting cedido (defesa por zona / faixa de distância)
    {"category": "shooting", "type": "by_zone_opponent"},
    {"category": "shooting", "type": "5ft_range_opponent"},
]

# Tipos de temporada extraídos para (team_)season_averages.
SEASON_TYPES = ["regular", "playoffs", "ist"]


def require_env(name: str) -> str:
    """Lê uma env var obrigatória, levantando erro claro se ausente/vazia.

    Helper de uso OPCIONAL (não levanta no import-time deste módulo): os scripts
    podem chamá-lo quando quiserem falhar cedo com mensagem acionável em vez de
    propagar um erro obscuro mais adiante.
    """
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return value


def _int_env(name: str, default: int) -> int:
    """Lê uma env var inteira com fallback, levantando erro claro se inválida.

    Helper de uso OPCIONAL (não chamado no import-time). Útil para janelas/limites
    configuráveis por ambiente sem espalhar try/except de int() pelos scripts.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"{name} inválido: {raw!r} (esperado inteiro)") from e


def use_backfill_seasons() -> bool:
    """Centraliza a leitura da flag BACKFILL_SEASONS.

    BACKFILL_SEASONS truthy → iterar todas as SEASONS (uso pontual/backfill).
    Sem a var (ou vazia) → só a SEASON corrente (padrão Cloud Run).
    """
    return bool(os.getenv("BACKFILL_SEASONS"))


def get_seasons_to_process() -> list:
    """Resolve as seasons a processar conforme a flag BACKFILL_SEASONS."""
    return SEASONS if use_backfill_seasons() else [SEASON]


# Status HTTP que, em (team_)season_averages, significam "combinação sem dados"
# (a balldontlie devolve 400/404/422 p/ combos category/type inexistentes naquela
# temporada/season_type) e devem ser tratados como skip, não como erro real.
HTTP_NO_DATA_STATUS = (400, 404, 422)


def is_http_no_data(exc) -> bool:
    """True se a exceção for um HTTPError de "sem dados" (400/404/422).

    Centraliza a classificação antes duplicada entre os scripts de season averages.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) if response is not None else None
    return status_code in HTTP_NO_DATA_STATUS


def get_mode(env_var: str, default: str = "current") -> str:
    """Centraliza a leitura de env vars de modo dos extractors de futebol.

    Ex.: get_mode("STANDINGS_MODE"). Mantém o default "current" usado hoje pelos
    scripts; passa a haver um único ponto caso a semântica de modo mude.
    """
    return os.getenv(env_var, default)


# GCS Path Structure
def get_gcs_path(
    endpoint: str,
    season: int,
    date: str = None,
    market: str = None,
    game_id: int = None,
    category: str = None,
    type: str = None,
    season_type: str = None,
    period: int = None,
    sport: str = "nba",
    mode: str = None,
) -> str:
    """
    Gera o caminho no GCS seguindo a estrutura definida.

    Args:
        endpoint: Nome do endpoint (ex: 'games', 'active_players')
        season: Ano da temporada (ex: 2025)
        date: Data no formato YYYY-MM-DD (opcional)
        market: Market para player_props (opcional)
        game_id: Game ID para player_props (opcional)
        category: Categoria para season_averages (opcional)
        type: Tipo para season_averages (opcional)
        season_type: Tipo de temporada para season_averages (ex: regular, playoffs, ist)
        period: Quarto/período (NBA) — grava em subdiretório q{period}/ quando combinado com date
        sport: Identificador do esporte ("nba" default, "futebol" para API-Football)
        mode: Sufixo de modo para endpoints futebol ("current"|"backfill"), opcional

    Returns:
        Caminho completo no formato: nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}.json
        ou nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{date}.json
        ou nba/{endpoint}/{season}/{market}/raw_nba_{endpoint}_{season}-{game_id}.json (para player_props)
        ou nba/{endpoint}/{season}/raw_nba_{endpoint}_{season}-{category}-{type}-{season_type}.json (para season_averages)
        ou futebol/{endpoint}/raw_futebol_{endpoint}{_mode}.json (para sport='futebol')
        ou futebol/{endpoint}/raw_futebol_{endpoint}{_mode}_{date}.json (futebol com date — snapshots diários)
    """
    # Branch dedicado para sport='futebol' (não polui a lógica NBA existente)
    if sport == "futebol":
        # Endpoints per-fixture (ex: fixture_statistics) salvam 1 arquivo por jogo,
        # reusando o param game_id como fixture_id:
        # futebol/{endpoint}/raw_futebol_{endpoint}_{fixture_id}.json
        # fixture_lineups grava em duas fases (mode "confirmed"|"real") → sufixo de fase
        # no nome do arquivo p/ guardar os dois snapshots (T-30min e pós-jogo).
        # odds grava em quatro janelas (mode "daily"|"t24h"|"t1h"|"t15m") → mesmo mecanismo de
        # sufixo; só a "daily" passa `date` (date-stamp por dia).
        if game_id is not None:
            # Fases válidas: janelas de odds + de predictions (fonte única: as constantes
            # FUTEBOL_*_WINDOWS) + fases de lineups ("confirmed"|"real"). Novas janelas
            # funcionam sem editar aqui.
            phase = (
                f"_{mode}"
                if mode in {"confirmed", "real", *FUTEBOL_ODDS_WINDOWS, *FUTEBOL_PREDICTIONS_WINDOWS, *FUTEBOL_INJURIES_WINDOWS}
                else ""
            )
            # `date` opcional: predictions date-stampa o snapshot por (fixture, janela, dia)
            # p/ recapturar 1x/dia (varredura de jogos futuros) — o fato dedup latest-wins por
            # loaded_at. Odds/lineups não passam date → datepart vazio → nomes atuais intactos.
            datepart = f"_{date}" if date else ""
            filename = f"raw_futebol_{endpoint}_{game_id}{phase}{datepart}.json"
            return f"futebol/{endpoint}/{filename}"
        # `date` opcional: endpoints de snapshot diário (ex.: standings) date-stampam
        # o arquivo p/ acumular histórico no GCS (1 arquivo/dia; re-run no mesmo dia
        # sobrescreve). Os demais endpoints não passam date → latest-only, como antes.
        suffix = f"_{mode}" if mode in ("backfill", "current") else ""
        datepart = f"_{date}" if date else ""
        filename = f"raw_futebol_{endpoint}{suffix}{datepart}.json"
        return f"futebol/{endpoint}/{filename}"

    if market and game_id:
        # Estrutura especial para player_props: nba/player_props/{season}/{market}/raw_nba_player_props_{season}-{game_id}.json
        filename = f"raw_nba_{endpoint}_{season}-{game_id}.json"
        return f"nba/{endpoint}/{season}/{market}/{filename}"
    elif game_id and not market:
        # Estrutura para endpoints com game_id sem vendor: nba/betting_odds/{season}/raw_nba_betting_odds_{season}-{game_id}.json
        filename = f"raw_nba_{endpoint}_{season}-{game_id}.json"
        return f"nba/{endpoint}/{season}/{filename}"
    elif category and type:
        # Estrutura para season_averages: nba/season_averages/{season}/raw_nba_season_averages_{season}-{category}-{type}-{season_type}.json
        suffix = f"-{season_type}" if season_type else ""
        filename = f"raw_nba_{endpoint}_{season}-{category}-{type}{suffix}.json"
        return f"nba/{endpoint}/{season}/{filename}"
    elif period and date:
        filename = f"raw_nba_{endpoint}_{season}-{date}.json"
        return f"nba/{endpoint}/{season}/q{period}/{filename}"
    elif date:
        filename = f"raw_nba_{endpoint}_{season}-{date}.json"
        return f"nba/{endpoint}/{season}/{filename}"
    else:
        filename = f"raw_nba_{endpoint}_{season}.json"
        return f"nba/{endpoint}/{season}/{filename}"

