# Code Review — smartbetting / data-engineering

> Revisão multi-agente (ultracode). 19 unidades revisadas em paralelo, cada achado verificado adversarialmente (um cético reabre o código e tenta refutar), depois sintetizado.

**Data:** 2026-06-25  ·  **Veredito geral:** `needs-attention`

**Métricas da revisão:**
- Unidades revisadas: **19**  ·  Agentes: **185**  ·  Tokens: **4.6M**  ·  Duração: **~27 min**
- Achados brutos: **165** → verificados → **125** mantidos (**37** refutados, **3** incertos)
- Relatório final (pós-dedup): **77** achados — 🟠 5 altos · 🟡 16 médios · 🔵 46 baixos · ⚪ 10 info
- Nenhum achado **crítico**.

---

## Sumário executivo

O pipeline de data-engineering (NBA + Futebol) esta funcionalmente operacional e bem modularizado segundo o .cursorrules, mas a revisao revelou um conjunto consistente de fragilidades em CORRETUDE DE DADOS e CONFIABILIDADE que merecem atencao antes de escalar campeonatos/temporadas. O risco mais agudo e estrutural: a coleta de odds adiciona a janela t15m (linha de fechamento / CLV real, forward-only e irrecuperavel) mas o whitelist hardcoded em get_gcs_path nunca foi atualizado, gerando nome de arquivo divergente da convencao documentada justamente para o dado mais critico do produto. Mais grave ainda e o padrao de MASCARAMENTO DE FALHAS: erros transitorios por jogo/fixture e estouro de quota da API-Football (HTTP 200 + errors) sao silenciosamente tratados como 'sem dados', os extractors retornam sucesso aparente, e os workflows nao tem gate de qualidade nem retry/backoff para 5xx/timeout. Extractors latest-only de futebol (fixtures, a tabela mae) podem sobrescrever o arquivo bom com coleta parcial quando uma liga falha, cascateando para todo o pipeline. Os workflows tambem sincronizam para o Postgres PRD mesmo quando o dbt falhou. Ha ainda exposicao de segredos: chaves de API pagas (BALLDONTLIE_KEY, API_FOOTBALL_KEY) injetadas em texto puro via --set-env-vars, divergindo do padrao Secret Manager ja adotado para Gmail/Supabase. O restante e um volume saudavel de debito de manutenibilidade: duplicacao NBA vs Futebol, divergencias de timezone, documentacao (README) e DDL desatualizados. Nenhum achado e catastrofico isoladamente, mas a combinacao de falhas silenciosas + ausencia de retry + sobrescrita destrutiva justifica classificar o sistema como needs-attention.

---

## Temas

### 🟠 Bug do sufixo de janela t15m nas odds (whitelist hardcoded em get_gcs_path)  _(Alto)_

FUTEBOL_ODDS_WINDOWS inclui a janela t15m (linha de fechamento p/ CLV real, forward-only), mas o whitelist de fases em get_gcs_path (src/config.py:365) so reconhece confirmed/real/t24h/t1h. Resultado: o snapshot t15m e gravado como raw_futebol_odds_{fixture}.json SEM sufixo, divergindo do contrato documentado (config.py:103). O dado ainda e ingerido (glob *.json + coluna collection_window no payload), entao nao ha perda/colisao real, mas quebra a convencao de nomenclatura justamente do dado mais critico e o whitelist e estruturalmente fragil (3 fontes de verdade desacopladas).

### 🟠 Mascaramento de falhas: erros e quota tratados como sucesso vazio  _(Alto)_

Padrao sistemico em ~15 extractors: excecao por jogo/fixture vira logger.error + continue sem contabilizar; estouro de quota da API-Football (HTTP 200 + campo errors) e tratado como 'sem dados'. Os metodos extract_and_save retornam apenas a lista de salvos, sem expor failed, e scripts/wrappers retornam sucesso (HTTP 200). Workflows nao distinguem 'temporada sem jogos' de 'metade falhou', gerando buracos silenciosos. Paginas 2..N de /players e /odds tambem nao checam errors, devolvendo coleta parcial como completa.

### 🟠 Sobrescrita destrutiva de arquivos latest-only com coleta parcial  _(Alto)_

Extractors latest-only de futebol (fixtures - a tabela mae -, leagues, players, teams, team_season_stats) iteram alvos (Brasileirao + Copa), pulam alvos que falharam com errors/vazio e mesmo assim sobrescrevem o arquivo unico no GCS. Se uma liga falha, o snapshot bom anterior e substituido por um parcial, e como fixtures alimenta odds/predictions/stats/lineups (forward-only), as janelas pre-jogo da liga ausente sao perdidas ate o proximo run bem-sucedido.

### 🟠 Paginacao incompleta em player_props/betting_odds (NBA)  _(Alto)_

get_player_props e get_betting_odds (balldontlie v2) fazem uma unica chamada e retornam data.get('data',[]) sem loop de paginacao, diferentemente de todos os outros endpoints que usam get_paginated com cursor/page. Props/odds alem da primeira pagina sao silenciosamente truncadas, e o extractor salva arquivo parcial como sucesso. Agravado por nao enviarem per_page=100.

### 🟡 Segredos e exposicao de credenciais  _(Médio)_

Chaves de API pagas (BALLDONTLIE_KEY, API_FOOTBALL_KEY) sao injetadas em ~26 servicos via --set-env-vars em texto puro, visiveis a qualquer principal com roles/run.viewer, divergindo do padrao --set-secrets (Secret Manager) ja usado para Gmail/Supabase. Wrappers de erro ecoam str(e) cru (potencial vazamento de host/schema do Postgres). Deploy faz 'set -a; source .env' exportando todos os segredos a subprocessos. .gitignore nao protege chaves JSON de service account.

### 🟡 Confiabilidade dos workflows: sem retry, sem gate de qualidade, sync de dados parciais  _(Médio)_

Nenhuma chamada http.get/jobs.run dos 9 workflows usa retry/backoff (sensivel a 429/5xx/cold start). O sync para Postgres PRD roda incondicionalmente mesmo quando o dbt (dbt-nba/dbt-futebol) falhou, publicando marts desatualizadas/parciais. A fase 2 (dbt) do workflow futebol roda mesmo quando todas as extracoes ou a extracao mae (fixtures) falharam. Branches paralelas capturam 'e' mas nao logam e.message.

### 🟡 Robustez do cliente HTTP (retry, backoff, timeout, parse)  _(Médio)_

O retry de _execute_request so cobre HTTP 429 — 5xx/timeout/ConnectionError falham na primeira tentativa. O wait do Retry-After nao tem teto (pode dormir horas). get_paginated nao re-tenta por pagina (perde tudo no meio). response.json() sem try/except quebra com corpo nao-JSON. A ultima tentativa duplica a requisicao fora do laco. SMTP_SSL sem timeout pode travar o handler.

### 🟡 Operacoes destrutivas/nao-atomicas em BigQuery e relatorio de status  _(Médio)_

create_external_table sempre faz delete+create nao-atomico: se o create falhar, a external table fica ausente ate novo run (downstream dbt/sync quebra). O relatorio de status marca todos os combos de season_averages como False quando um falha. create_external_tables.py (futebol) aborta as demais tabelas na primeira excecao, diferente do isolamento por endpoint do lado NBA.

### 🟡 Corretude de dados no sync e tratamento de datas  _(Médio)_

No sync BQ->Postgres, STRING vazia colide com NULL no COPY (NULL '') — string vazia real do BQ vira NULL no destino. Carimbos de data inconsistentes: NBA usa datetime.now() naive enquanto futebol usa datetime.now(timezone.utc); varios extractors usam datetime.utcnow() (deprecado no 3.12+). Em borda de meia-noite UTC pode particionar no dia errado.

### 🟡 Violacoes de camada e config nao centralizada (.cursorrules)  _(Médio)_

Listas de combinations/season_types, BACKFILL_SEASONS, *_MODE, dataset sandbox e project ID hardcoded estao nos scripts em vez de src/config.py. get_external_table_uri duplica a montagem de caminho que pertence a get_gcs_path (escrita vs leitura precisam sincronizar manualmente; ja diverge no branch q{period}). BigQuery project/dataset/location hardcoded sem env por ambiente.

### 🔵 Duplicacao e drift NBA vs Futebol (DRY)  _(Baixo)_

extract_and_save e _get_season_date_range duplicados quase verbatim entre 4 extractors NBA por data (ja causou divergencia real no range de datas). 3 extractors per-fixture de futebol sao copia-cola (~70 linhas x3). Scripts/servicos de player_props por vendor identicos exceto VENDOR. ~28 requirements.txt identicos dificultam bump de CVE. Politicas divergentes: skip-if-exists e time.sleep(0.4) no futebol mas nao nos per-game NBA; 3 padroes de propagacao de mode nos wrappers.

### 🔵 Validacao de configuracao e falhas de startup  _(Baixo)_

API_KEY/API_FOOTBALL_KEY/GCP_PROJECT_ID lidos sem validacao (falha tardia obscura). int(os.getenv('SEASON')) sem try/except derruba o import. daily_summary nao valida formato de date (erro de cliente vira 500). Secrets lidos em nivel de modulo em notify_execution quebram cold start com KeyError.

### 🔵 Documentacao e DDL desatualizados  _(Baixo)_

README documenta flags CLI inexistentes (--season/--dates), script inexistente (full_load.py) e omite todo o vertical futebol/sync/daily_summary. create_external_tables.sql tem project/dataset/location hardcoded e diverge das tabelas geradas pelo Python (3 geradores de DDL). Comentarios/descriptions de DDL e docstrings ainda citam '2 janelas' de odds ignorando t15m.

### ⚪ Performance e eficiencia  _(Info)_

blob.exists() pos-upload e get_table chamado 2x por tabela geram round-trips extras. skip-if-exists faz N chamadas exists() por fixture. Validacao de NDJSON re-parseia todo o payload. Sync materializa tabela inteira em StringIO (copia dupla). Leitura full-download de blobs sem streaming. Extracoes futebol estritamente sequenciais vs paralelo no NBA.

### ⚪ Code smells e codigo morto  _(Info)_

Parametro 'type' sombreia builtin; flag use_cursor_pagination so loga; branch GCS_USE_ADC com ramos identicos; aviso de '0 registros' da base e dead code para extractors por data; parse_date com formatos ambiguos e nao usado; servico extract_player_props orfao; .gitignore referencia cloud_functions inexistente.

---

## Achados detalhados

## 🟠 Severidade: Alto

### 1. Janela de odds t15m nao recebe sufixo no nome do arquivo GCS (linha de fechamento/CLV)

- **Arquivo:** `src/config.py:365`
- **Categoria:** data-correctness  ·  **Confiança:** high  ·  **Tema:** Bug do sufixo de janela t15m nas odds (whitelist hardcoded em get_gcs_path)

**Problema:** O OddsExtractor coleta em 3 janelas (FUTEBOL_ODDS_WINDOWS = t24h, t1h, t15m) e passa mode=window tanto no skip-if-exists (odds_extractor.py:125-127) quanto no upload (odds_extractor.py:155-162). Porem get_gcs_path so adiciona o sufixo de fase quando mode in ('confirmed','real','t24h','t1h') — 't15m' NAO esta na lista. Resultado: a janela t15m grava em raw_futebol_odds_{fixture}.json SEM sufixo, divergindo do contrato documentado (config.py:103) e da descricao da external table. A t15m e a linha de FECHAMENTO p/ CLV real, forward-only. O dado ainda e ingerido (external table le via glob *.json e collection_window esta no payload), entao nao ha perda/colisao real hoje, mas a nomenclatura do dado mais critico fica inconsistente e o whitelist e estruturalmente fragil — derivado de 3 fontes de verdade desacopladas (FUTEBOL_ODDS_WINDOWS, e dois whitelists hardcoded em config.py:365 e 371).

**Recomendação:** Incluir 't15m' no whitelist da linha 365. Melhor: substituir o whitelist fragil por phase = f"_{mode}" if mode else "" no branch sport='futebol' com game_id, ou derivar as fases validas de set(FUTEBOL_ODDS_WINDOWS) | {"confirmed","real"}, para que novas janelas funcionem sem editar config em dois lugares. Adicionar teste parametrizado cobrindo todas as chaves de FUTEBOL_ODDS_WINDOWS. Atualizar comentarios/descriptions de DDL que ainda citam '2 janelas'.

### 2. Rate limit por quota diaria da API-Football (HTTP 200 + errors) tratado como 'sem dados'

- **Arquivo:** `src/clients/api_football_client.py:18-22, 132-153, 254-289`
- **Categoria:** data-correctness  ·  **Confiança:** medium  ·  **Tema:** Mascaramento de falhas: erros e quota tratados como sucesso vazio

**Problema:** A API-Football sinaliza estouro de quota DIARIA com HTTP 200 e o campo errors preenchido (response vazio), nao 429. O retry em _execute_request so reage a 429. Os extractors (ex.: odds_extractor.py:56-59, fixture_statistics_extractor.py:49-52) apenas logam e retornam total=0, que cai no ramo 'vazio: nao grava, re-tenta depois', NAO contado como failed. O run termina com SUCESSO aparente com dados faltando. Pior no OddsExtractor (forward-only): se a quota estoura, os polls da janela retornam vazio, a banda T-1h/t15m fecha e o snapshot e perdido PERMANENTEMENTE sem alerta.

**Recomendação:** Detectar no cliente quando errors contem indicador de rate limit/quota (ex.: chave 'rateLimit'/'requests') e tratar como condicao recuperavel: aguardar/retentar ou levantar excecao especifica para o orquestrador falhar o run. No minimo, distinguir 'sem dados' de 'erro de quota' para nao mascarar lacunas, contabilizando errors como 'failed' e nao 'empty'.

### 3. Falha parcial por data/jogo engolida (except: continue) sem sinalizar falha no retorno

- **Arquivo:** `src/extractors/game_player_stats_extractor.py:145-159`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Mascaramento de falhas: erros e quota tratados como sucesso vazio

**Problema:** Em todos os extractors com loop (games, stats, stats_period, advanced_stats, player_props, betting_odds e os per-fixture de futebol) cada iteracao tem 'except Exception: logger.error(...); continue'. Um erro transitorio (timeout, 5xx que estourou os retries, JSON malformado) e apenas logado e a iteracao pulada, mas extract_and_save retorna a lista de paths salvos como se tudo desse certo. O orquestrador nao distingue 'temporada sem jogos' de 'metade das datas/fixtures falhou', resultando em buracos silenciosos. Mitigado parcialmente por re-execucoes idempotentes diarias, mas uma indisponibilidade prolongada de GCS ou bug sistemico passa despercebido.

**Recomendação:** Acumular as datas/jogos que falharam (failed_count) e expor no retorno. No orquestrador, falhar o processo quando failed_count exceder limite (ex.: failed>0 e saved==0, ou failed/total acima de X%). No minimo logar um ERROR de resumo distinto de 'datas sem dados' quando failed_count > 0.

### 4. player_props e betting_odds NAO paginam: dados alem da 1a pagina perdidos silenciosamente

- **Arquivo:** `src/clients/balldontlie_client.py:296-306, 323-333`
- **Categoria:** data-correctness  ·  **Confiança:** medium  ·  **Tema:** Paginacao incompleta em player_props/betting_odds (NBA)

**Problema:** get_player_props e get_betting_odds fazem uma unica chamada _execute_request e retornam data.get('data', []), sem loop de paginacao nem leitura de meta.next_cursor/total_pages — diferente de todos os outros endpoints que passam por get_paginated. Qualquer resposta paginada e truncada na primeira pagina. Agravante: nem enviam per_page=100 (definido em ENDPOINT_CONFIGS mas nunca repassado), usando o page-size default. O extractor trata lista nao-vazia como sucesso e salva arquivo parcial. A propria equipe ja constatou no dominio futebol que odds vem paginadas (merge de bookmakers), mas o NBA nao foi corrigido.

**Recomendação:** Rotear ambos por get_paginated (que ja trata cursor/page) ou implementar loop explicito lendo meta.next_cursor/total_pages. No minimo, logar a meta retornada e alertar/levantar quando houver next_cursor nao consumido, para nao salvar arquivos parciais sem aviso.

### 5. Extractors latest-only sobrescrevem o arquivo do GCS mesmo com falha parcial de uma liga

- **Arquivo:** `src/extractors/fixtures_extractor.py:37-89`
- **Categoria:** data-correctness  ·  **Confiança:** high  ·  **Tema:** Sobrescrita destrutiva de arquivos latest-only com coleta parcial

**Problema:** FixturesExtractor.extract itera os targets (Brasileirao + Copa) e, se um retornar envelope com errors ou response vazio, faz 'continue' mas segue gravando o arquivo unico raw_futebol_fixtures_current.json so com as ligas sobreviventes. fixtures e a TABELA MAE (get_fixture_ids_from_storage/get_upcoming_fixtures_with_kickoff leem esse arquivo para alimentar odds/predictions/statistics/events/lineups/player_stats). Se uma liga ja populada falhar com errors transitorio, o snapshot completo anterior e sobrescrito por um parcial e os jogos dessa liga somem do pipeline ate o proximo run; como odds/predictions sao forward-only, as janelas pre-jogo sao perdidas. Mesmo padrao em leagues/team_season_stats. (Erro de REDE levanta e aborta sem sobrescrever; o caso destrutivo e o envelope errors/vazio.)

**Recomendação:** Para arquivos latest-only que sao fonte de verdade: se algum target obrigatorio falhar (errors/vazio inesperado), NAO sobrescrever o arquivo bom. Opcoes: (a) abortar com raise (workflow marca PARTIAL_FAILURE, arquivo antigo preservado); ou (b) merge com o conteudo existente, substituindo apenas ligas coletadas com sucesso. Distinguir 'liga sem dados legitimamente' de 'liga falhou'.

## 🟡 Severidade: Médio

### 6. Sync para Postgres PRD ocorre mesmo quando o dbt falhou

- **Arquivo:** `workflow_data_engineering.yml:292-388`
- **Categoria:** data-correctness  ·  **Confiança:** high  ·  **Tema:** Confiabilidade dos workflows: sem retry, sem gate de qualidade, sync de dados parciais

**Problema:** O connector jobs.run aguarda a LRO, entao falhas duras do dbt SAO detectadas e marcadas em failed_services com PARTIAL_FAILURE. Porem a phase3_sync_supabase_prd/dev roda INCONDICIONALMENTE — nao ha switch consultando failed_services. Mesmo com dbt-nba em failed_services, as marts (potencialmente desatualizadas/parciais, pois cada modelo e CREATE OR REPLACE atomico) sao empurradas para o Postgres PRD. Identico em workflow_bets e workflow_injury_report e na fase 2 do workflow_futebol.

**Recomendação:** Introduzir flag dbt_ok=true/false e um switch antes da phase3 para pular o sync PRD (no minimo) quando o dbt falhar, evitando publicar dados nao construidos/stale em producao.

### 7. Fase 2 (dbt) do workflow futebol roda mesmo quando todas as extracoes falharam

- **Arquivo:** `workflow_futebol.yml:451-501`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Confiabilidade dos workflows: sem retry, sem gate de qualidade, sync de dados parciais

**Problema:** Cada fase de extracao (1..1j) captura sua excecao, marca PARTIAL_FAILURE e segue. A fase 2 (dbt-futebol) esta num try independente que NAO consulta failed_services nem workflow_status. Se extract-fixtures (1d) ou varias extracoes falharem, o dbt ainda roda sobre external tables potencialmente defasadas, propagando dados velhos. Nao ha gate de qualidade (saved_count>0) como existe nos workflows de odds/lineups/predictions. As fases 1e/1f/1g/1h dependem de 1d (leem o arquivo de fixtures) mas tambem rodam mesmo se 1d falhar, processando fixtures defasados.

**Recomendação:** Adicionar gate antes da fase 2: se 'extract-fixtures' (ou qualquer extracao mae) estiver em failed_services, pular o dbt ou rodar subset seguro com log de WARNING. Idealmente as fases 1e-1h tambem so devem rodar se 1d teve sucesso.

### 8. Ausencia de politica de retry/backoff nas chamadas HTTP/job de todos os workflows

- **Arquivo:** `workflow_futebol.yml:45-446`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Confiabilidade dos workflows: sem retry, sem gate de qualidade, sync de dados parciais

**Problema:** Nenhuma das chamadas http.get nem jobs.run dos 9 workflows usa retry/backoff (grep por retry/backoff/predicate retorna vazio). Servicos dependem de APIs externas (balldontlie/API-Football, sujeitas a 429/5xx) e de Cloud Run (cold start). Uma falha transitoria derruba a fase e marca PARTIAL_FAILURE; nos workflows de poll forward-only (odds/lineups/predictions) o snapshot daquela janela e perdido permanentemente. Os except ja degradam graciosamente, mas sem retry a intermitencia evitavel vira dado faltante.

**Recomendação:** Adicionar retry com backoff exponencial as chamadas http.get e jobs.run, com predicate que reexecute em 429/502/503/504 e erros de conexao (ex.: retry: {predicate: ${http.default_retry_predicate}, max_retries: 3, backoff: {initial_delay:5,max_delay:60,multiplier:2}}). Critico nos workflows forward-only.

### 9. STRING vazia do BigQuery vira NULL no Postgres (corrupcao silenciosa)

- **Arquivo:** `src/sync/bq_to_postgres.py:197-212, 299-303`
- **Categoria:** data-correctness  ·  **Confiança:** high  ·  **Tema:** Corretude de dados no sync e tratamento de datas

**Problema:** O COPY usa NULL '' e _format_value converte None em string vazia. O csv.writer nao quota campos vazios, entao None e uma STRING literalmente '' geram o mesmo token e ambos sao inseridos como NULL no Postgres. Uma coluna que no BQ valia '' passa a valer NULL no destino. O parity check de schema nao pega (estrutura valida). Ocorre apenas se as marts contiverem strings literalmente vazias distintas de NULL.

**Recomendação:** Usar sentinela explicito para NULL que nao colida com string vazia: definir NULL '\\N' no COPY e em _format_value retornar esse token so para None, mantendo '' como ''. Alternativamente, usar copy tipado do psycopg3 (copy.write_row) em vez de CSV textual.

### 10. README massivamente desatualizado: flags CLI inexistentes, script inexistente e zero mencao ao vertical futebol/sync

- **Arquivo:** `README.md:293-329`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Documentacao e DDL desatualizados

**Problema:** O README documenta execucao com flags --season/--dates/etc. que NAO existem (argparse e proibido), referencia scripts/full_load.py inexistente, e nao menciona o vertical inteiro de futebol (API-Football, scripts/futebol/, cloud_run/futebol/), o sync BQ->Postgres nem o daily_summary. CLAUDE.md ja avisa staleness, mas a magnitude torna o README ativamente enganoso para onboarding.

**Recomendação:** Reescrever a secao de uso para refletir a execucao real (sem flags; config via .env/src/config.py), remover referencias a full_load.py e flags, e adicionar secoes para futebol, sync Postgres e daily_summary. No minimo alinhar com o bloco 'Common commands' do CLAUDE.md.

### 11. Idempotencia inconsistente: extractors per-game NBA refazem tudo a cada run; futebol tem skip-if-exists

- **Arquivo:** `src/extractors/betting_odds_extractor.py:58-79`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** BettingOddsExtractor e PlayerPropsExtractor iteram TODOS os game_ids (props x vendors) a cada run e regravam o GCS sem skip-if-exists, enquanto os per-fixture de futebol implementam skip-if-exists + janela de re-fetch. Para uma temporada inteira sao milhares de chamadas reprocessando jogos antigos cujas odds nunca mudam — desperdicio de quota e tempo crescente, com risco de tangenciar o timeout de 3600s perto do fim da temporada. Nao corrompe dados, mas e ineficiente e arriscado.

**Recomendação:** Aplicar a mesma mecanica skip-if-exists + janela de re-fetch de N dias dos extractors de futebol aos per-game NBA (betting_odds e player_props), pulando jogos cujo arquivo ja existe exceto os recentes.

### 12. Quatro extractors por data duplicam _get_season_date_range e extract_and_save (ja causou divergencia de range)

- **Arquivo:** `src/extractors/game_player_stats_extractor.py:69-159`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** _get_season_date_range e extract_and_save sao copiados quase verbatim entre games, game_player_stats, game_player_stats_period e game_player_advanced_stats (mesmo loop por data, try/except por iteracao, skipped_dates, logs '='*60). Ja causou divergencia REAL: games usa end_date hardcoded datetime(season+1,6,30) enquanto os outros usam NBA_SEASON_END_DATES.get(season) com fallback datetime.now(). Qualquer correcao (dedup, paginacao, fuso) precisa ser replicada em 4 lugares.

**Recomendação:** Promover um extract_and_save_by_date generico para a BaseExtractor (recebendo a chave do array e o range), deixando cada subclasse so especializar extract(). Unificar o range em NBA_SEASON_END_DATES + fallback datetime.now() para todos.

### 13. Erros nas paginas 2..N de /players e /odds nao tratados — coleta parcial silenciosa

- **Arquivo:** `src/clients/api_football_client.py:132-153, 254-289`
- **Categoria:** reliability  ·  **Confiança:** medium  ·  **Tema:** Mascaramento de falhas: erros e quota tratados como sucesso vazio

**Problema:** Em get_players e get_odds os errors so sao capturados na pagina 1 (first_errors). Se a API estourar quota a partir da pagina 2, o envelope dessas paginas vira {response: []} e o loop apenas faz extend(response or []) e segue, devolvendo errors=None (sucesso aparente) com MENOS itens do que paging.total. O players_extractor entao salva (latest-only, sobrescrevendo o arquivo bom) um conjunto PARCIAL de jogadores como completo, corrompendo dim_players sem sinal de erro.

**Recomendação:** Checar envelope.get('errors') em TODA pagina; se vier erro/quota a partir da pagina N, propagar (errors preenchido ou excecao) em vez de devolver coleta parcial. Alternativamente validar len(all_items) coerente com paging.total e abortar o save quando paginas faltarem.

### 14. create_external_table faz delete+create nao-atomico: se o create falhar, a tabela fica ausente

- **Arquivo:** `src/bigquery/bigquery_client.py:137-158`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Operacoes destrutivas/nao-atomicas em BigQuery e relatorio de status

**Problema:** create_external_table sempre faz delete_table antes de create_table (para forcar re-autodetect). Se o create falhar (URI sem arquivos, schema invalido, erro transitorio/IAM), a external table fica ausente ate novo run, quebrando downstream dbt/sync. Em create_all_external_tables o except por endpoint apenas marca results=False e segue. Mitigado: external tables sao so metadados (recriacao barata/idempotente) e o script chamador retorna exit code 1 quando ha falha, entao nao e silencioso no nivel de script.

**Recomendação:** Criar a nova tabela com nome temporario e fazer swap, ou capturar a falha do create e restaurar/alertar de forma destacada. No agregador, considerar abortar com codigo de erro quando uma tabela essencial falhar.

### 15. Retry/backoff so cobre HTTP 429 — 5xx, timeouts e erros de conexao falham na primeira tentativa

- **Arquivo:** `src/clients/base_client.py:47-72`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Robustez do cliente HTTP (retry, backoff, timeout, parse)

**Problema:** _execute_request so faz retry quando status_code == 429. Qualquer 500/502/503/504 (comuns sob carga) ou requests.exceptions.Timeout/ConnectionError levanta imediatamente, sem reenvio. Em loops grandes (games NBA ~250 chamadas/dia; fixtures/odds por jogo) e nos polls, um unico hiccup aborta a chamada — nos forward-only o ultimo poll antes do kickoff perde a janela de fechamento.

**Recomendação:** Estender o retry para status >= 500 e excecoes transitorias (Timeout, ConnectionError, ChunkedEncodingError), reusando _RETRY_BACKOFFS. Idealmente usar urllib3 Retry num HTTPAdapter na Session (status_forcelist=[429,500,502,503,504], respeitando Retry-After) para cobrir tambem get_paginated e o cliente futebol de forma uniforme.

### 16. Backoff fixo de ate 930s e Retry-After sem teto podem dormir minutos/horas por requisicao

- **Arquivo:** `src/clients/base_client.py:10, 47-72`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Robustez do cliente HTTP (retry, backoff, timeout, parse)

**Problema:** _RETRY_BACKOFFS = [30,60,120,240,480] soma 930s de sleep bloqueante numa unica chamada. Pior: wait = int(response.headers.get('Retry-After', backoff)) e usado direto sem teto — um Retry-After grande (ex.: cota diaria em segundos) faz time.sleep bloquear por horas, estourando timeout (3600s no Cloud Run) e queimando instancia faturada. Em loops por fixture, sequencias de backoff agregadas podem matar o run sem checkpoint.

**Recomendação:** Aplicar teto ao wait (ex.: min(wait, 120)) e revisar a escala dos backoffs. Garantir que o orcamento total de retry seja compativel com o timeout do Cloud Run. Considerar backoff exponencial curto + jitter.

### 17. Falha na Logging API derruba todo o resumo (sem fallback), ao contrario da Executions API

- **Arquivo:** `src/reporting/daily_summary.py:319-323`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Robustez do cliente HTTP (retry, backoff, timeout, parse)

**Problema:** collect_crashed_executions (fonte secundaria) esta protegida por try/except e o codigo segue 'so com Logging' se falhar. Ja collect_from_logging (fonte PRIMARIA) e chamada sem protecao. Se a Logging API lancar excecao, run_daily_summary propaga, build_html nem roda e NENHUM email e enviado naquele dia — deixando o operador as cegas no canal de monitoramento. Assimetria de robustez incoerente. Vira HTTP 500 visivel, mas exige monitorar outro canal.

**Recomendação:** Envolver collect_from_logging em try/except, logando o erro e seguindo para build_html/send_email com o que houver agregado (inclusive a parte da Executions API), idealmente com aviso 'Logging indisponivel' no corpo.

### 18. Chaves de API (BALLDONTLIE_KEY, API_FOOTBALL_KEY) injetadas como env vars em texto puro

- **Arquivo:** `scripts/deploy_cloud_run.sh:225-234, 376`
- **Categoria:** security  ·  **Confiança:** high  ·  **Tema:** Segredos e exposicao de credenciais

**Problema:** build_env_vars monta BALLDONTLIE_KEY e API_FOOTBALL_KEY e o branch padrao de deploy passa essas chaves via --set-env-vars em texto puro para ~26 servicos de extracao. Em contraste, notify-execution/sync-bq-to-postgres/daily-summary usam corretamente --set-secrets (Secret Manager). Env vars de servico Cloud Run sao visiveis para qualquer principal com roles/run.viewer e persistem na revisao, vazando as credenciais das APIs pagas para um circulo de acesso muito mais amplo que o necessario. Exposicao exige acesso IAM ao projeto (nao e publica), o que limita o blast radius.

**Recomendação:** Migrar ambas as chaves para o Secret Manager e injeta-las com --set-secrets (mesma mecanica de GMAIL_APP_PASSWORD/SUPABASE_PG_URL_*). Manter em --set-env-vars apenas valores nao sensiveis (GCS_BUCKET_NAME, GCP_PROJECT_ID, SEASON, LOG_LEVEL). src.config ja le ambas via os.getenv, sem mudanca de codigo Python.

### 19. Construtor do GCSStorage faz exists()/create_bucket a cada instanciacao (IAM amplo + latencia)

- **Arquivo:** `src/storage/gcs_storage.py:44-58`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Segredos e exposicao de credenciais

**Problema:** Toda construcao de GCSStorage faz bucket.exists() (round-trip por cold start) e, se nao existir, create_bucket. Isso exige que a service account tenha storage.buckets.create — privilegio amplo que viola minimo privilegio para um servico que so deveria gravar objetos. Um typo em GCS_BUCKET_NAME criaria silenciosamente um bucket novo (regiao/policy default) em vez de falhar de forma ruidosa. Ha try/except Forbidden que apenas re-lanca, sem eliminar a necessidade da permissao.

**Recomendação:** Remover a criacao automatica do runtime (provisionar via IaC/Terraform com regiao/policies corretas). No construtor usar apenas self.client.bucket(name) (lazy) e, se quiser validacao, raise explicito caso nao exista. Conceder a SA somente storage.objectAdmin no bucket.

### 20. Configuracao de negocio (combinations/season_types) hardcoded nos scripts em vez de src/config.py

- **Arquivo:** `scripts/extract_team_season_averages.py:15-40`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Violacoes de camada e config nao centralizada (.cursorrules)

**Problema:** COMBINATIONS (20 pares) em extract_team_season_averages.py e a lista combinations (4) em extract_season_averages.py sao copias exatas de TEAM_SEASON_AVERAGES_COMBINATIONS/SEASON_AVERAGES_COMBINATIONS em bigquery_client.py (191-216, 184-189), com comentarios identicos. season_types=['regular','playoffs','ist'] duplicado nos dois scripts. A extracao (script) e a criacao da external table (bigquery_client) precisam ser mantidas em sincronia manual; um novo type so num lugar gera tabela apontando para arquivos inexistentes ou vice-versa. Hoje estao sincronizados (risco de drift futuro).

**Recomendação:** Mover SEASON_AVERAGES_COMBINATIONS, TEAM_SEASON_AVERAGES_COMBINATIONS e SEASON_TYPES para src/config.py (fonte unica) e importar tanto nos scripts quanto em bigquery_client.py.

### 21. get_external_table_uri duplica a logica de montagem de caminho de get_gcs_path

- **Arquivo:** `src/bigquery/bigquery_client.py:38-83`
- **Categoria:** architecture  ·  **Confiança:** high  ·  **Tema:** Violacoes de camada e config nao centralizada (.cursorrules)

**Problema:** get_external_table_uri e get_player_props_uris reconstroem manualmente a estrutura de caminhos GCS (gs://{bucket}/nba/{endpoint}/{season}/...-{category}-{type}-*.json) que e canonica em get_gcs_path. Como get_gcs_path produz o caminho de ESCRITA e get_external_table_uri o glob de LEITURA, ambos precisam sincronizar manualmente. Ja ha divergencia: o branch q{period} (escrita em subdiretorio) nao tem equivalente na leitura, gerando glob que nao casa com arquivos dentro de q{period}/.

**Recomendação:** Extrair a parte de prefixo/diretorio de get_gcs_path para um helper reutilizavel (get_gcs_prefix) e fazer get_external_table_uri montar o glob a partir dele, garantindo fonte unica da verdade para a estrutura de caminhos.

## 🔵 Severidade: Baixo

### 22. .gitignore referencia diretorio inexistente (cloud_functions) em vez de cloud_run

- **Arquivo:** `.gitignore:28-29`
- **Categoria:** consistency  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** O .gitignore ignora 'cloud_functions/*/src/', mas o projeto usa 'cloud_run/'. O deploy copia src/ para um mktemp, entao normalmente nao polui o repo; a regra atual e cosmetica/incorreta e nao cobriria a subarvore aninhada cloud_run/futebol/*.

**Recomendação:** Corrigir para 'cloud_run/**/src/' e 'cloud_run/**/scripts/', removendo a entrada obsoleta cloud_functions.

### 23. Pins de dependencia inconsistentes/abertos (requests==2.31.0 com CVE, functions-framework==3.*, bigquery, python 3.14)

- **Arquivo:** `requirements.txt:2-4`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** requests==2.31.0 (CVE-2024-35195) fixado em ~28 arquivos — porem o codigo nunca usa verify=False, entao o vetor nao e alcancavel (higiene de SCA). functions-framework==3.* e tzdata sem versao tornam builds nao reproduziveis. google-cloud-bigquery diverge entre raiz (>=3.25.0) e sync (==3.27.0). .python-version=3.14.2 vs sync pinado em 3.13 (psycopg sem wheel cp314) e demais servicos sem pin de runtime.

**Recomendação:** Atualizar requests para >=2.32.3; pinar versoes exatas de functions-framework e tzdata; alinhar google-cloud-bigquery entre raiz e servicos; padronizar GOOGLE_RUNTIME_VERSION em todos os servicos e alinhar o .python-version. Idealmente adotar lock file por servico.

### 24. Servico extract_player_props orfao: diretorio existe mas nao esta no deploy nem nos workflows

- **Arquivo:** `scripts/deploy_cloud_run.sh:25-39`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** Existe cloud_run/extract_player_props/ (com main.py e requirements.txt), mas NBA_SERVICES so registra as variantes -draftkings/-caesars/-betrivers e nenhum workflow chama o servico generico. Codigo morto que pode confundir manutencao (dev editar achando estar em producao) ou um deploy manual antigo ficar rodando defasado.

**Recomendação:** Remover cloud_run/extract_player_props/ se foi substituido pelas variantes, ou adiciona-lo a NBA_SERVICES se ainda deve ser deployado. Confirmar que nao ha servico orfao rodando em producao.

### 25. Falta set -u e pipefail nos scripts de deploy

- **Arquivo:** `scripts/deploy_cloud_run.sh:5`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** Ambos os scripts usam apenas 'set -e'. Sem 'set -u', um typo em nome de variavel expande para vazio silenciosamente (ex.: gcloud com flag vazia). As obrigatorias ja sao validadas explicitamente, entao o risco residual e typo em variavel usada adiante. Hardening defensivo.

**Recomendação:** Trocar por 'set -euo pipefail' em ambos, revisando usos de variaveis opcionais com ${VAR:-} onde a ausencia for legitima.

### 26. Uso de self.client.dataset()/dataset_ref.table() (API legada/deprecada do BigQuery)

- **Arquivo:** `src/bigquery/bigquery_client.py:98, 134-135`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** self.client.dataset(self.dataset_id) esta deprecado em versoes recentes do SDK (gera DeprecationWarning, risco em upgrade futuro). Ainda funciona em producao. Note que dataset_ref.table(table_id) em si nao e deprecado.

**Recomendação:** Migrar para bigquery.DatasetReference(project, dataset).table(table_id) ou usar identificadores totalmente qualificados f'{project}.{dataset}.{table}' nas chamadas get/create/delete_table.

### 27. max_items=1000 pode truncar silenciosamente jogadores ativos da NBA

- **Arquivo:** `src/clients/balldontlie_client.py:214-229`
- **Categoria:** data-correctness  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** get_active_players usa max_items=1000; se ultrapassar, get_paginated trunca a lista e so emite logger.warning (nao falha), propagando coleta incompleta aos marts. O teto realista de jogadores ativos NBA fica bem abaixo de 600, entao o limite de 1000 e ~2x e o truncamento e improvavel hoje. Fragilidade defensiva.

**Recomendação:** Elevar/remover o limite (o cursor termina sozinho) ou transformar o estouro de max_items em erro/alerta acionavel em vez de warning silencioso.

### 28. get_paginated muta o dict de params do chamador (efeito colateral latente)

- **Arquivo:** `src/clients/base_client.py:120-134`
- **Categoria:** bug  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** params = params or {} nao copia e o metodo muta in-place (params['per_page']=..., pop('page'), params['cursor']=...). Todos os chamadores atuais passam dicts recem-criados, entao nao explode, mas se algum reutilizar o mesmo dict herdara parametros de paginacao espurios. Divergencia de contrato com o cliente futebol (que monta params novos por pagina).

**Recomendação:** Copiar o dict no inicio: params = dict(params or {}). Sem mudanca de comportamento.

### 29. Flag use_cursor_pagination setada mas nao influencia a logica

- **Arquivo:** `src/clients/base_client.py:117, 176-185`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** use_cursor_pagination so serve para emitir um log uma unica vez; a decisao real de modo (cursor vs page) e tomada a cada iteracao olhando next_cursor/total_pages, nao a flag. Sugere uma maquina de estados (trava de modo) que nao existe. Removê-la cruamente mudaria o comportamento de logging (log repetiria por pagina).

**Recomendação:** Remover a flag e logar a mudanca de modo de forma simples, ou implementar de fato uma trava de modo se a intencao for evitar alternancia.

### 30. Colisao de path em season_averages/team_season_averages quando season_type e None

- **Arquivo:** `src/config.py:384-388`
- **Categoria:** data-correctness  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** No ramo 'category and type', o sufixo de season_type so e incluido 'if season_type'. Se um chamador passar season_type=None/'' diferentes tipos (regular/playoffs/playin) escreveriam no MESMO blob, sobrescrevendo-se. Os extractors sempre passam default ('regular'/'advanced'), entao nao ocorre na pratica, mas a assinatura permite None sem validacao.

**Recomendação:** Tornar season_type obrigatorio nesse ramo (validar/levantar se ausente) ou usar placeholder, evitando colisao silenciosa entre tipos de temporada.

### 31. Aviso de '0 registros' da BaseExtractor.extract_and_save e dead code para os extractors por data

- **Arquivo:** `src/extractors/base_extractor.py:88-110`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** BaseExtractor.extract_and_save implementa contagem (record_count), warning de 0 registros e resolucao de date a partir de has_date. Porem todos os endpoints com has_date=True (games, stats, props, etc.) sobrescrevem extract_and_save inteiro, entao o ramo 'if self.has_date' nunca dispara — apenas active_players/player_injuries/team_standings (e season_averages via save_to_gcs) exercem a base. Divergencia entre contrato anunciado e execucao real.

**Recomendação:** Reposicionar a logica comum (contagem, warning, resolucao de date) para um helper reutilizado pelos overrides, ou simplificar a base para refletir que serve so aos endpoints latest-only. Remover o ramo has_date se nenhum endpoint has_date usa o caminho base.

### 32. Workflow daily compartilha o mesmo Cloud Run Job (dbt-futebol) com 3 workflows de poll

- **Arquivo:** `workflow_futebol_odds.yml:109-117`
- **Categoria:** reliability  ·  **Confiança:** medium  ·  **Tema:** Code smells e codigo morto

**Problema:** Os 4 workflows futebol disparam executions do MESMO job dbt-futebol (polls a cada ~15min + diario full), com modelos sobrepostos (fact_odds_snapshot/fact_predictions_api no diario e nos polls). Cloud Run Jobs nao serializam executions. Para modelos 'table' (CREATE OR REPLACE atomico) o resultado e last-writer-wins benigno; o dano concreto (locks/corrupcao) e especulativo (materializacao dbt nao versionada no repo).

**Recomendação:** Desencavalar janelas (agendar o diario fora dos slots de poll) e/ou remover +fact_odds_snapshot/+fact_predictions_api do select diario (ja cobertos pelos workflows proprios). Considerar lock leve ou concorrencia controlada no job.

### 33. Variavel de excecao 'e' capturada mas nao logada nas branches paralelas de extracao

- **Arquivo:** `workflow_data_engineering.yml:49-55`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Confiabilidade dos workflows: sem retry, sem gate de qualidade, sync de dados parciais

**Problema:** Nas branches paralelas o except captura 'as: e' mas nao registra e.message; apenas adiciona o servico a failed_services. Quando um servico falha, nao ha rastro do motivo (status HTTP, corpo) nos logs do workflow, dificultando diagnostico — diferente das fases sequenciais (phase2/phase3) que logam e.message. Vale para workflow_data_engineering e workflow_bets.

**Recomendação:** Adicionar sys.log severity=ERROR com error: ${e.message} em cada handler de branch antes de atualizar failed_services, mantendo paridade com o logging das fases sequenciais.

### 34. Uso inconsistente de datetime.now() naive (NBA) vs datetime.now(timezone.utc) (futebol) e utcnow() deprecado

- **Arquivo:** `src/extractors/base_extractor.py:104`
- **Categoria:** data-correctness  ·  **Confiança:** high  ·  **Tema:** Corretude de dados no sync e tratamento de datas

**Problema:** Extractors NBA derivam a data de datetime.now() (naive) em base_extractor:104, games_extractor:64, etc.; futebol usa datetime.now(timezone.utc) para snapshot_date e datetime.utcnow() (deprecado no 3.12+; venv e 3.14) para loaded_at em ~11 extractors. No Cloud Run TZ=UTC coincide, mas em execucao local (Sao Paulo, UTC-3) o now() naive pode particionar no dia errado na borda de meia-noite. loaded_at e TIMESTAMP no BQ (mesmo instante UTC em ambos os formatos), entao o dedup nao quebra; impacto e cosmetico + deprecacao.

**Recomendação:** Centralizar 'agora' num helper unico (ex.: src/utils/helpers.now_utc() retornando datetime.now(timezone.utc)) e usa-lo em TODOS os extractors dos dois dominios. Trocar datetime.utcnow() por datetime.now(timezone.utc) em todo o codigo.

### 35. Tabela carregada inteira em memoria (StringIO) antes do COPY

- **Arquivo:** `src/sync/bq_to_postgres.py:284-303`
- **Categoria:** performance  ·  **Confiança:** high  ·  **Tema:** Corretude de dados no sync e tratamento de datas

**Problema:** Todo o conteudo da tabela e materializado em io.StringIO e depois copy.write(buf.getvalue()) materializa a string inteira de novo (pico de memoria duplo). Le-se tudo do BQ primeiro e so depois TRUNCATE+COPY (aumentando a janela de lock). O servico tem 1Gi e as tabelas NBA ficam na ordem de dezenas de MB, entao OOM e improvavel hoje; e ineficiencia evitavel.

**Recomendação:** Fazer streaming: dentro de 'with cur.copy(...) as copy:' iterar diretamente sobre rows_iter e chamar copy.write_row(...) (API tipada do psycopg3, que serializa tipos e dispensa o CSV manual e o problema de NULL/empty-string). Elimina o buffer e reduz o lock.

### 36. Skip-if-unchanged pode deixar Postgres permanentemente desatualizado apos drift de estado

- **Arquivo:** `src/sync/bq_to_postgres.py:268-274`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Corretude de dados no sync e tratamento de datas

**Problema:** A decisao de pular baseia-se so em comparar bq_table.modified com last_synced gravado em _sync_state, sem verificar contagem/parity de linhas no destino. Se a tabela PG for truncada/recriada fora do sync sem mexer no state, o sync pula indefinidamente ate o BQ ser modificado, mantendo o PG inconsistente. Nao ha flag force/full-resync. Auto-recuperavel quando a mart e reconstruida (modified muda) e corrigivel manualmente (deletar linha do state).

**Recomendação:** Oferecer parametro force/full-resync e/ou validar contagem destino vs origem ao pular. No minimo expor um modo de forcar re-sync; considerar invalidar o state se a tabela destino estiver vazia mas o BQ tiver linhas.

### 37. Extracao vazia mascarada como sucesso nos scripts de snapshot futebol, divergindo do padrao NBA

- **Arquivo:** `scripts/futebol/extract_standings.py:20-29`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Documentacao e DDL desatualizados

**Problema:** standings/injuries chamam extract_and_save() e logam '✓ Concluido: {gcs_path}' retornando 0 sempre; o extractor retorna '' quando zero linhas, gerando '✓ Concluido: ' (path vazio) reportado como sucesso. Os scripts NBA equivalentes emitem logger.warning('Nenhum arquivo foi salvo'). Os extractors ja logam WARNING proprio, entao nao e totalmente silencioso, mas o resumo diario nao captura.

**Recomendação:** Padronizar com os scripts NBA: tratar retorno vazio ('' ou []) emitindo logger.warning, ou propagar saved_count/status ao log_completion para o resumo diario capturar.

### 38. create_external_tables.sql com project/dataset/location hardcoded e divergente do gerador Python

- **Arquivo:** `scripts/sql/create_external_tables.sql:15-298`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Documentacao e DDL desatualizados

**Problema:** O .sql referencia smartbetting-dados.nba e location='us-east1' literalmente em 30+ lugares (apenas bucket/season templatizados) e ja DIVERGE do que o Python gera (nao tem raw_game_player_advanced_stats, raw_player_props_caesars/betrivers nem shooting *_opponent; tem playtypes/tracking_passing que o Python pode nao ter). Existem 3 geradores de DDL (BigQueryClient NBA, create_external_tables.py futebol, e este .sql). O .sql nao e referenciado por nenhum workflow/deploy mas e documentado como executavel no README, criando footgun de DDL desatualizado.

**Recomendação:** Marcar o .sql como DEPRECATED/nao-fonte-de-verdade no header (ou remove-lo), templatizar tambem project/dataset/location, e consolidar a definicao no caminho Python.

### 39. Duplicacao de requirements identicos em ~28 servicos dificulta correcao de CVE

- **Arquivo:** `cloud_run/extract_active_players/requirements.txt:1-4`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** 27 servicos repetem o mesmo bloco (google-cloud-storage==2.14.0, requests==2.31.0, python-dotenv==1.0.0, functions-framework==3.*). O deploy copia o requirements.txt proprio de cada servico; nao ha geracao a partir de fonte unica. Um bump (ex.: corrigir CVE em requests) exige editar 27 arquivos com risco de divergencia silenciosa.

**Recomendação:** Centralizar dependencias comuns (ex.: requirements-common.txt referenciado via '-r' no deploy, ou gerar os requirements a partir de fonte unica no deploy script).

### 40. Tres padroes divergentes de propagacao de 'mode' entre os wrappers cloud_run de futebol

- **Arquivo:** `cloud_run/futebol/extract_fixtures/main.py:29-33`
- **Categoria:** consistency  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** Os wrappers usam tres mecanismos para passar mode: (1) maioria seta os.environ['<X>_MODE']=mode (estado global mutavel; sob concorrencia default 80 do Cloud Run, duas requisicoes com mode diferente na mesma instancia podem sobrescrever-se — janela estreita, hoje sempre sobrescrito com default); (2) fixture_lineups passa mode=... ao construtor; (3) odds/predictions instanciam o extractor sem mode. Acoplamento por string de env var, fragil a typo. Workflows chamam em serie, entao a corrida e cenario de excecao.

**Recomendação:** Padronizar para o estilo (2): wrapper le request.args.get('mode') e passa explicitamente no construtor do extractor, sem mutar os.environ. Se a chamada via main() for desejada, fazer main() aceitar mode como parametro. Alternativamente setar --concurrency=1 nesses servicos.

### 41. Inconsistencia arquitetural: parte dos wrappers de futebol chama Extractor direto em vez de main()

- **Arquivo:** `cloud_run/futebol/extract_odds/main.py:19-36`
- **Categoria:** consistency  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** extract_odds/predictions/fixture_lineups instanciam o Extractor diretamente para devolver saved_count (necessario para o gate de dbt do workflow), enquanto os demais wrappers chamam main() do script e so ecoam status. Dois padroes convivem; a divergencia e intencional e documentada nos comentarios, mas aumenta a chance de drift entre 20+ wrappers.

**Recomendação:** Padronizar: ou expor saved_count via retorno do main() (dict/contagem) e todos os wrappers chamam main(); ou todos chamam o Extractor direto. Documentar a escolha unica.

### 42. Quatro scripts + quatro servicos Cloud Run para player_props que diferem apenas no vendor

- **Arquivo:** `scripts/extract_player_props_draftkings.py:1-35`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** extract_player_props_draftkings/_caesars/_betrivers sao byte-a-byte identicos exceto docstring e VENDOR; cada um tem diretorio cloud_run e servico separado. A lista de vendors ja vive em ENDPOINT_CONFIGS['player_props']['vendors']. Qualquer ajuste (logging/erro) precisa ser replicado 4x. As branches paralelas dos workflows justificam isolamento de execucao, mas nao 3 scripts/wrappers identicos.

**Recomendação:** Manter um unico script lendo o vendor de uma env var (ex.: PLAYER_PROPS_VENDOR, a exemplo do FIXTURES_MODE); os servicos Cloud Run apontam para o mesmo codigo variando so a env var no deploy.

### 43. Duplicacao massiva do bloco create_external_table no DDL de futebol (13 chamadas quase identicas)

- **Arquivo:** `scripts/futebol/create_external_tables.py:39-643`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** O main() repete 13x o padrao: montar {endpoint}_uri = f"gs://{bucket}/futebol/{endpoint}/*.json", chamar bq.create_external_table e logar. URI e table_id sao 100% derivaveis do endpoint. Os schemas explicitos justificam variacao, mas a montagem de URI+log+chamada e boilerplate que facilita divergencias (como o drift de t15m).

**Recomendação:** Modelar uma lista de specs [(endpoint, schema, description), ...] e iterar, derivando uri e table_id no loop. Espelha create_all_external_tables() do BigQueryClient.

### 44. Divergencia de paginacao entre clientes NBA e Futebol sem abstracao comum

- **Arquivo:** `src/clients/api_football_client.py:115-153, 231-289`
- **Categoria:** consistency  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** O cliente futebol reimplementa manualmente a paginacao (page=1..paging.total) em get_players e get_odds, com leitura de paging, time.sleep(0.5) e montagem de envelope sintetico duplicados entre os dois metodos, enquanto o NBA usa get_paginated. A justificativa (envelope {paging,response} vs {data,meta}) e legitima, mas a duplicacao tende a divergir.

**Recomendação:** Extrair um helper no BaseClient para paginacao por paging.total (ex.: get_paginated_envelope) recebendo a funcao de merge, reutilizado por get_players e get_odds, centralizando delay e contagem de paginas.

### 45. Sem delay entre chamadas nos loops per-game NBA, enquanto futebol usa time.sleep(0.4)

- **Arquivo:** `src/extractors/betting_odds_extractor.py:58-74`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** Loops per-fixture de futebol inserem time.sleep(0.4) anti-rate-limit; os per-game NBA (betting_odds, player_props) disparam uma requisicao por jogo/vendor sem delay, dependendo so do retry de 429 (custo de 30-480s por 429). Inconsistencia direta de politica de rate-limit entre dominios (clientes diferentes, parcialmente justificavel). Sem perda de dados, apenas lentidao/risco potencial.

**Recomendação:** Padronizar a cortesia entre chamadas: introduzir uma constante unica (ex.: API_COURTESY_DELAY em config) aplicada nos loops dos dois dominios, ou centralizar o delay no proprio cliente HTTP.

### 46. extract_and_save de fixture_statistics/events/player_stats sao copia-cola (~70 linhas triplicadas)

- **Arquivo:** `src/extractors/fixture_events_extractor.py:80-152`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Duplicacao e drift NBA vs Futebol (DRY)

**Problema:** Os tres extractors per-fixture pos-jogo (FixtureStatistics, FixtureEvents, FixturePlayerStats) tem extract_and_save praticamente byte-a-byte iguais (mesmo loop, skip-if-exists, janela is_recent, try/except, contadores, time.sleep(0.4), msg final), variando so o endpoint e a chave de total. Uma correcao de robustez aplicada num pode ser esquecida nos outros.

**Recomendação:** Extrair um metodo template no BaseExtractor (ou classe PerFixtureExtractor) recebendo endpoint_name, extract(fixture_id), a chave de contagem e o seletor de fixtures. Cada concreto so implementa extract(). FixtureLineups (com ramo pregame e sufixo) fica fora do template ou o especializa.

### 47. Falha parcial nao sinalizada: 1 tabela quebrada aborta as demais no DDL de futebol

- **Arquivo:** `scripts/futebol/create_external_tables.py:24-32, 646-648`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Operacoes destrutivas/nao-atomicas em BigQuery e relatorio de status

**Problema:** Todo o main() esta sob um unico try/except com 13 chamadas create_external_table sequenciais; a primeira excecao pula para o except (return 1) e as tabelas seguintes nao sao criadas — diferente do BigQueryClient.create_all_external_tables (NBA), que isola cada endpoint e devolve dict de status por tabela. Como create_external_table e atomico por tabela (delete+create), nao ha perda silenciosa de varias tabelas, mas perde-se visibilidade de sucesso parcial e bloqueia as seguintes naquele run.

**Recomendação:** Iterar com try/except por tabela (acumulando status num dict como no lado NBA), logar resumo ao final e retornar !=0 apenas se houve falha, sempre tentando criar TODAS as tabelas independentes.

### 48. Status de resultado incorreto: falha em um combo marca todos os combos como False

- **Arquivo:** `src/bigquery/bigquery_client.py:224-258, 307-335, 495-506`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Operacoes destrutivas/nao-atomicas em BigQuery e relatorio de status

**Problema:** Para season_averages e team_season_averages, todas as combinacoes sao criadas dentro de um unico try. No except, o codigo marca TODOS os combos como False, mesmo os ja criados com sucesso antes do que falhou. O resumo final reporta tabelas existentes em producao como falhas. Problema apenas de observabilidade — as tabelas criadas continuam no BigQuery.

**Recomendação:** Mover o try/except para dentro do loop (um try por combo) e marcar results apenas para o combo que falhou, ou pre-inicializar False e setar True apos cada create bem-sucedido, deixando o except sem reescrever os True.

### 49. skip-if-exists faz uma chamada blob.exists() por fixture — N round-trips no backfill

- **Arquivo:** `src/extractors/fixture_statistics_extractor.py:112-118`
- **Categoria:** performance  ·  **Confiança:** high  ·  **Tema:** Performance e eficiencia

**Problema:** Os 6 extractors per-fixture chamam self.storage.bucket.blob(blob_path).exists() dentro do loop. Em backfill de uma temporada (centenas/milhares de fixtures) isso gera 1 round-trip por fixture so para o skip-check, dominante em re-runs com muitos skips. Ja existe list_blobs por prefixo no proprio GCSStorage.

**Recomendação:** Pre-carregar o conjunto de blobs existentes com bucket.list_blobs(prefix='futebol/{endpoint}/') uma vez no inicio do extract_and_save e testar pertinencia em memoria.

### 50. blob.exists() apos upload gera chamada extra de rede e log enganoso

- **Arquivo:** `src/storage/gcs_storage.py:133-141`
- **Categoria:** performance  ·  **Confiança:** high  ·  **Tema:** Performance e eficiencia

**Problema:** Apos cada upload bem-sucedido, o codigo faz blob.exists() (requisicao HTTP extra ao GCS) so para logar. Se upload_from_string retornou sem excecao, o objeto foi gravado; a checagem dobra a latencia por arquivo e, em alta cardinalidade (player_props/odds, 1 arquivo por jogo), multiplica chamadas. O ramo 'else' (warning) e praticamente inalcancavel.

**Recomendação:** Remover o blob.exists() pos-upload e confiar na ausencia de excecao. Se quiser integridade, usar o md5/crc32c retornado pelo proprio upload.

### 51. Extracoes futebol rodam estritamente sequenciais (10 fases), divergindo do paralelo do NBA

- **Arquivo:** `workflow_futebol.yml:45-446`
- **Categoria:** performance  ·  **Confiança:** high  ·  **Tema:** Performance e eficiencia

**Problema:** O NBA executa extracoes em parallel/branches; o futebol executa as 10 fases em serie, cada uma com timeout 1800s. Fases independentes (leagues/teams/players/standings/injuries) nao precisam ser sequenciais. Alem de tempo de parede maior, ha inconsistencia arquitetural. Batch diario (wall-time nao critico) e ha justificativa plausivel (rate-limit da API-Football).

**Recomendação:** Paralelizar as fases independentes num bloco parallel com shared:[failed_services, workflow_status], mantendo as dependentes (fixture_* apos 1d) numa segunda etapa. Alinha com o padrao NBA.

### 52. Resposta sem corpo JSON (204/HTML) levanta excecao nao tratada em .json()

- **Arquivo:** `src/clients/base_client.py:138`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Robustez do cliente HTTP (retry, backoff, timeout, parse)

**Problema:** Em get_paginated, response.json() e chamado sem protecao (idem balldontlie_client.py:298 e :326). Se a API responder 2xx com corpo vazio ou nao-JSON, json() levanta JSONDecodeError (subclasse de ValueError, nao RequestException), propagando erro pouco descritivo. raise_for_status ja mitiga 4xx/5xx com HTML; resta o caso raro de 200/204 vazio. Falha ruidosa/fail-fast, sem corrupcao.

**Recomendação:** Envolver o parse com try/except ValueError/JSONDecodeError logando status e inicio do corpo (truncado, sem segredos), ou validar Content-Type antes de json().

### 53. get_paginated nao tem retry por pagina e pode abortar a coleta inteira no meio

- **Arquivo:** `src/clients/base_client.py:123-214`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Robustez do cliente HTTP (retry, backoff, timeout, parse)

**Problema:** O loop de paginacao chama _execute_request por pagina; como o retry so cobre 429, uma falha 5xx/timeout na pagina N descarta TODAS as N-1 paginas ja coletadas em all_data, refazendo tudo do zero. O fallback len(items) < per_page so e alcancavel quando a API nao retorna cursor/total_pages (raro na balldontlie). Consequencia: desperdicio/abortos, nao corrupcao persistida.

**Recomendação:** Cobrir 5xx/timeout no retry para que falhas de pagina sejam reenviadas em vez de abortarem. Onde a API expoe total_count, validar ao final que len(all_data) bate e logar WARNING em caso de divergencia.

### 54. _execute_request refaz a requisicao apos o laco de retry (duplicacao + chamada extra)

- **Arquivo:** `src/clients/base_client.py:47-72`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Robustez do cliente HTTP (retry, backoff, timeout, parse)

**Problema:** Apos esgotar _RETRY_BACKOFFS, o metodo repete manualmente self.session.request(...) + raise_for_status() fora do laco. Isso duplica codigo (risco de divergencia futura) e faz uma 6a chamada (sem tratamento de 429) imediatamente apos um sleep de ate 480s. O log 'X/6' esta consistente (contagem correta). Funciona, mas e fragil.

**Recomendação:** Unificar num unico laco de N tentativas onde a ultima iteracao nao dorme e propaga o erro. Elimina a duplicacao.

### 55. Chamadas SMTP sem timeout podem travar o handler de notificacao/resumo

- **Arquivo:** `src/reporting/daily_summary.py:308-310`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Robustez do cliente HTTP (retry, backoff, timeout, parse)

**Problema:** smtplib.SMTP_SSL('smtp.gmail.com', 465) e chamado sem timeout (idem cloud_run/notify_execution/main.py:36, que tambem nao tem try/except e este servico nao e mais invocado por workflows). Sem timeout, handshake/login/sendmail pode bloquear o socket; o pior caso e limitado pelo timeout de request do Cloud Run e o resumo roda 1x/dia. Caminho vivo (daily-summary) ja protegido pelo wrapper e workflow.

**Recomendação:** Passar timeout explicito (ex.: SMTP_SSL(host, 465, timeout=30)) em ambos os pontos e envolver o envio em try/except que loga e nao deixa a excecao derrubar o handler silenciosamente.

### 56. .gitignore nao protege arquivos de credenciais JSON / chaves de service account

- **Arquivo:** `.gitignore:1-29`
- **Categoria:** security  ·  **Confiança:** high  ·  **Tema:** Segredos e exposicao de credenciais

**Problema:** O .gitignore cobre .env mas nao cobre chaves de service account em JSON (*-key.json, service-account*.json, *credentials*.json) nem o caminho de GOOGLE_APPLICATION_CREDENTIALS. O projeto usa ADC + impersonation (sem chave em uso atual), entao e risco preventivo, mas um dev pode baixar uma chave JSON para a raiz e commita-la por engano.

**Recomendação:** Adicionar padroes como '*-key.json', '*credentials*.json', 'service-account*.json', 'sa-*.json'. Idealmente proibir chaves JSON de SA (usar somente ADC/impersonation) e adicionar hook de pre-commit (ex.: gitleaks).

### 57. Respostas de erro expoem str(e) ao chamador (vazamento de detalhe interno)

- **Arquivo:** `cloud_run/sync_bq_to_postgres/main.py:42-43`
- **Categoria:** security  ·  **Confiança:** high  ·  **Tema:** Segredos e exposicao de credenciais

**Problema:** O handler retorna {"status":"error","error":str(e)} no corpo HTTP 500. Em sync, str(e) de psycopg/conexao pode conter host/usuario do Supabase ou nomes de schema; nos demais, caminhos internos. O endpoint exige OIDC (--no-allow-unauthenticated), limitando o blast radius, mas o detalhe vaza para logs do workflow e principals autorizados.

**Recomendação:** Logar a excecao completa server-side (logger.error exc_info=True) e retornar mensagem generica + id de correlacao, sem ecoar str(e). Aplicar consistentemente; garantir que a DSN nunca apareca em mensagens de erro.

### 58. Deploy faz 'set -a; source .env' exportando todos os segredos a subprocessos

- **Arquivo:** `scripts/deploy_cloud_run.sh:172-175`
- **Categoria:** security  ·  **Confiança:** high  ·  **Tema:** Segredos e exposicao de credenciais

**Problema:** deploy_cloud_run.sh e deploy_workflows.sh fazem 'set -a; source .env; set +a', exportando TODAS as variaveis do .env (incluindo SUPABASE_PG_URL_PRD/DEV com senha e as chaves de API) para o ambiente do processo de deploy e de todos os subprocessos gcloud (que podem loga-las em modo verbose). Como o sync ja usa Secret Manager, exporta-las no shell e desnecessario. Operacao local/manual, baixo impacto.

**Recomendação:** Carregar do .env apenas as variaveis necessarias para o deploy (GCP_PROJECT_ID, GCS_BUCKET_NAME, SEASON, LOG_LEVEL, SERVICE_ACCOUNT) ou parsear linha-a-linha so as chaves esperadas, evitando exportar SUPABASE_PG_URL_* e chaves de API globalmente.

### 59. daily_summary nao valida formato de date e retorna 500 em erro de cliente

- **Arquivo:** `cloud_run/daily_summary/main.py:21-27`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Validacao de configuracao e falhas de startup

**Problema:** O parametro date e passado direto para date.fromisoformat dentro do try generico; um valor invalido (ex.: ?date=2026-13-40) levanta ValueError capturado pelo except que retorna 500 (conceitualmente erro de cliente 400). Falhas de validacao de input e falhas reais ficam indistinguiveis no monitoramento. Idem o param env do sync, nao validado contra prd/dev.

**Recomendação:** Validar date_arg separadamente e retornar 400 com mensagem clara em formato invalido, deixando 500 apenas para falhas de execucao. Validar env do sync de forma analoga.

### 60. Segredos lidos em nivel de modulo causam crash de cold start (KeyError) se faltar env var

- **Arquivo:** `cloud_run/notify_execution/main.py:7-9`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Validacao de configuracao e falhas de startup

**Problema:** GMAIL_USER, GMAIL_APP_PASSWORD e NOTIFY_EMAIL sao lidos com os.environ[...] no escopo do modulo. Se um secret nao estiver montado (erro de deploy/rotacao), o import falha com KeyError e o container nao sobe, em vez de devolver erro HTTP claro. Diagnostico obscuro. Servico interno; falha visivel como revisao nao-saudavel.

**Recomendação:** Ler os segredos dentro do handler (lazy) ou validar a presenca no inicio do handler retornando 500 com mensagem clara (ex.: 'config ausente: GMAIL_APP_PASSWORD').

### 61. notify_execution sem try/except no SMTP; servico orfao nao invocado por workflows

- **Arquivo:** `cloud_run/notify_execution/main.py:31-40`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Validacao de configuracao e falhas de startup

**Problema:** Diferente de todos os outros wrappers, o handler nao envolve o envio SMTP em try/except; uma falha (Gmail indisponivel, app password revogada) devolve HTTP 500 com traceback nao tratado, e nao ha timeout no SMTP_SSL. Porem o servico nao e mais invocado por nenhum workflow (substituido pelo resumo diario consolidado), entao o caminho descrito nao e mais alcancavel — codigo efetivamente orfao.

**Recomendação:** Padronizar o try/except retornando {status:error},500 e adicionar timeout=30 ao SMTP_SSL por higiene, ou remover o servico se realmente obsoleto.

### 62. Falta de validacao de env vars obrigatorias (API keys, GCP_PROJECT_ID) no carregamento da config

- **Arquivo:** `src/config.py:15,20,169`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Validacao de configuracao e falhas de startup

**Problema:** API_KEY, API_FOOTBALL_KEY e GCP_PROJECT_ID sao lidos com os.getenv sem default nem validacao. Se faltar, o valor fica None e o erro so aparece tarde (header de auth None -> 401, ou GCS/BigQuery caindo no projeto default da ADC), produzindo falhas obscuras. get_pg_url ja segue o padrao correto (RuntimeError com mensagem clara); o resto nao. Em Cloud Run a revision sobe e so falha no 1o request.

**Recomendação:** Adicionar validacao lazy no ponto de uso (clients) ou um helper require_env(name) que levante RuntimeError com mensagem clara. Evitar import-time raise por causa do split NBA vs futebol.

### 63. int(os.getenv('SEASON')) sem tratamento de erro derruba todo import da config

- **Arquivo:** `src/config.py:231`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Validacao de configuracao e falhas de startup

**Problema:** SEASON = int(os.getenv('SEASON','2025')) — se SEASON for nao-numerico (ex.: '2025-26') ou string vazia, o int() levanta ValueError no import, derrubando qualquer importador de src.config (logger, extractors). O erro 'invalid literal for int()' nao indica que e a env var SEASON. Baixa probabilidade (config de operador) e baixo impacto.

**Recomendação:** Envolver a conversao com tratamento que produza mensagem clara (ex.: RuntimeError(f'SEASON invalido: {raw!r}') capturando ValueError), ou usar helper get_int_env.

### 64. Nome do dataset sandbox e project ID hardcoded nos scripts

- **Arquivo:** `scripts/create_sandbox_external_tables.py:13`
- **Categoria:** architecture  ·  **Confiança:** high  ·  **Tema:** Violacoes de camada e config nao centralizada (.cursorrules)

**Problema:** SANDBOX_DATASET='sandbox' definido no script (analogo a BIGQUERY_DATASET/BIGQUERY_DATASET_FUTEBOL ja em config). Alem disso o project 'smartbetting-dados' aparece hardcoded em logs (create_bigquery_external_tables.py:21 e create_sandbox_external_tables.py:23, f-string sem interpolacao) quando deveria vir de GCP_PROJECT_ID. Sem impacto funcional, favorece divergencia.

**Recomendação:** Adicionar BIGQUERY_DATASET_SANDBOX em src/config.py e importar; logar f"Projeto: {GCP_PROJECT_ID}" importando de src.config.

### 65. Tratamento de erro HTTP (400/404/422 = skip) duplicado entre scripts de season averages

- **Arquivo:** `scripts/extract_season_averages.py:50-57`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Violacoes de camada e config nao centralizada (.cursorrules)

**Problema:** O bloco que inspeciona e.response.status_code in (400,404,422) para classificar como 'skipped' esta duplicado quase identicamente em extract_season_averages.py e extract_team_season_averages.py. E logica de classificacao de resposta da API que deveria residir em src/ (extractor ou util), nao nos orquestradores finos. Risco de divergencia (ex.: API passar a usar 204).

**Recomendação:** Encapsular a regra 'HTTP X = sem dados (skip)' no extractor ou helper de src/utils, retornando um status para o script apenas registrar/contabilizar.

### 66. Leitura de configuracao (BACKFILL_SEASONS, *_MODE) via os.getenv direto nos scripts

- **Arquivo:** `scripts/futebol/extract_fixtures.py:22`
- **Categoria:** architecture  ·  **Confiança:** high  ·  **Tema:** Violacoes de camada e config nao centralizada (.cursorrules)

**Problema:** 11 scripts de futebol leem o modo com os.getenv('<X>_MODE','current') e 3 scripts NBA leem os.getenv('BACKFILL_SEASONS') diretamente no nivel de modulo, espalhando nomes literais de env var e o default por dezenas de arquivos + wrappers. Contraria o padrao config-driven (.cursorrules manda carregar env vars via src/config.py). Sem impacto funcional; duplicacao/divergencia.

**Recomendação:** Centralizar a leitura/normalizacao em src/config.py (ex.: get_fixtures_mode()/get_target_seasons() ou dict de defaults) e importar nos scripts, removendo os.getenv ad hoc e o import os.

### 67. BigQuery project/dataset/location hardcoded em config (sem env var por ambiente)

- **Arquivo:** `src/config.py:173-176`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Violacoes de camada e config nao centralizada (.cursorrules)

**Problema:** BIGQUERY_PROJECT_ID, BIGQUERY_DATASET, BIGQUERY_DATASET_FUTEBOL e BIGQUERY_LOCATION estao hardcoded (sem os.getenv), diferente de GCS_BUCKET_NAME/GCP_PROJECT_ID. Nao ha como apontar para projeto/dataset de dev/staging sem editar codigo, dificultando testes isolados e elevando risco de escrita acidental em prod. Inconsistente com o cuidado PRD/DEV do get_pg_url.

**Recomendação:** Permitir override via env var com default para os valores atuais (ex.: os.getenv('BIGQUERY_PROJECT_ID', 'smartbetting-dados')), mantendo compatibilidade e habilitando ambientes alternativos.

## ⚪ Severidade: Info

### 68. Retorno de sucesso sem status code explicito gera inconsistencia entre wrappers

- **Arquivo:** `cloud_run/extract_games/main.py:32-35`
- **Categoria:** consistency  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** Nos wrappers NBA o sucesso retorna apenas o dict (200 implicito), enquanto o erro retorna tupla (dict, 500); wrappers especiais (daily_summary/sync/odds) retornam tupla explicita (dict, 200) no sucesso. Comportamento correto, mas inconsistente entre ~25 wrappers.

**Recomendação:** Adotar tupla explicita (dict, 200) tambem no sucesso, ou extrair um helper comum run_pipeline_response(main) para os wrappers identicos.

### 69. deploy_cloud_run.sh exige BALLDONTLIE_KEY mesmo para deploy somente-futebol

- **Arquivo:** `scripts/deploy_cloud_run.sh:178-181`
- **Categoria:** reliability  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** load_env aborta se BALLDONTLIE_KEY faltar (fatal), enquanto API_FOOTBALL_KEY e so warning, e build_env_vars sempre injeta BALLDONTLIE_KEY em TODOS os servicos, inclusive os 13 de futebol que so usam API_FOOTBALL_KEY. Assimetria entre as chaves; deploy futebol-only impossivel sem chave NBA, e segredo NBA propagado a servicos que nao usam.

**Recomendação:** Tornar a exigencia de chave condicional ao esporte sendo deployado e injetar em cada servico apenas a(s) chave(s) que ele usa.

### 70. Parametro 'type' sombreia builtin e docstrings/typing desatualizados em get_gcs_path e get_external_table_uri

- **Arquivo:** `src/config.py:327, 389-391`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** O parametro 'type' sombreia o builtin em get_gcs_path (config.py:327) e get_external_table_uri (bigquery_client.py:53), sem efeito funcional. A docstring de get_gcs_path nao documenta 'period' nem o branch q{period} (389-391), e tipa opcionais como str sem Optional. game_id ja esta documentado.

**Recomendação:** Renomear 'type' para stat_type/type_param e anotar opcionais como Optional[str]=None. Atualizar a docstring para cobrir period e o branch q{period}.

### 71. Branch GCS_USE_ADC no GCSStorage tem os dois ramos identicos (dead code)

- **Arquivo:** `src/storage/gcs_storage.py:27-41`
- **Categoria:** maintainability  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** O if GCS_USE_ADC / else executa exatamente o mesmo codigo (storage.Client(project=...) ou storage.Client()), com o else so tendo um comentario sobre service account key. GCS_USE_ADC e constante True. Dead code que sugere uma flexibilidade de autenticacao que nao existe.

**Recomendação:** Reduzir para um unico caminho (self.client = storage.Client(project=GCP_PROJECT_ID) if GCP_PROJECT_ID else storage.Client()), removendo o ramo morto e o comentario, ou implementar de fato a alternativa por chave.

### 72. Interpolacao de nomes de tabela/coluna no SQL do sync — seguro hoje por allowlist

- **Arquivo:** `src/sync/bq_to_postgres.py:228-235, 298-312`
- **Categoria:** security  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** CREATE/TRUNCATE/COPY montam SQL via f-string interpolando table_name/colunas. NAO e injetavel hoje: table_name vem de MART_TABLES_ORDERED (resolve_tables valida o seletor HTTP contra essa allowlist e levanta ValueError), colunas vem do schema do BQ, valores vao por COPY/placeholders. Debito defensivo: a seguranca depende inteiramente da allowlist a montante.

**Recomendação:** Manter resolve_tables como invariante e adicionar assercao defensiva (assert table_name in MART_TABLES_ORDERED) e/ou usar psycopg.sql.Identifier, tornando a seguranca explicita e independente do chamador.

### 73. parse_date aceita formatos ambiguos (mes/dia) e e codigo nao utilizado

- **Arquivo:** `src/utils/helpers.py:21-35`
- **Categoria:** data-correctness  ·  **Confiança:** high  ·  **Tema:** Code smells e codigo morto

**Problema:** parse_date tenta '%d-%m-%Y' antes de '%m-%d-%Y'; uma entrada como '03-04-2025' seria interpretada como 3 de abril e nunca 4 de marco, sem aviso, e a mensagem de erro ('Use formato YYYY-MM-DD') contradiz os 6 formatos. Porem grep confirma que parse_date nao tem nenhum chamador — e dead code; o impacto descrito (corromper particionamento) nao se materializa.

**Recomendação:** Remover a funcao nao utilizada, ou restringir aos formatos realmente usados pelas fontes (idealmente so '%Y-%m-%d') e corrigir a mensagem de erro.

### 74. Comentarios/descriptions de DDL e docstrings citam '2 janelas' de odds ignorando t15m

- **Arquivo:** `scripts/futebol/create_external_tables.py:533-535, 554, 571-574`
- **Categoria:** consistency  ·  **Confiança:** high  ·  **Tema:** Documentacao e DDL desatualizados

**Problema:** Comentarios e a description da raw_futebol_odds afirmam '2 janelas (t24h|t1h)', e a docstring do handler cloud_run de odds (main.py:24-28) diz 'snapshot em 2 janelas T-24h e T-1h', mas FUTEBOL_ODDS_WINDOWS tem 3 janelas (t24h, t1h, t15m). O wildcard *.json cobre t15m e collection_window e STRING, entao nao ha bug de dados — apenas documentacao enganosa que pode levar consumidores a ignorar a linha de fechamento.

**Recomendação:** Atualizar comentarios, descriptions e docstrings para mencionar as 3 janelas (ou derivar de FUTEBOL_ODDS_WINDOWS), apos a correcao do bug de sufixo t15m.

### 75. Validacao de NDJSON faz parse duplo de todo o payload

- **Arquivo:** `src/storage/gcs_storage.py:112-123`
- **Categoria:** performance  ·  **Confiança:** high  ·  **Tema:** Performance e eficiencia

**Problema:** Apos serializar para NDJSON com json.dumps, o codigo re-parseia cada linha com json.loads para validar. Como as linhas foram geradas pelo proprio json.dumps, a validacao e redundante e adiciona um parse extra. Cargas sao por jogo/fixture (centenas a poucos milhares de linhas), dominadas pelo I/O do upload; custo de CPU desprezivel.

**Recomendação:** Remover a re-validacao por json.loads (confiar no json.dumps), mantendo apenas a checagem de string vazia. Validar so em dev/teste se quiser salvaguarda.

### 76. Leitura full-download de blobs em memoria, sem streaming

- **Arquivo:** `src/storage/gcs_storage.py:371, 454, 510, 567, 634`
- **Categoria:** performance  ·  **Confiança:** high  ·  **Tema:** Performance e eficiencia

**Problema:** Os metodos de leitura fazem blob.download_as_text() carregando o arquivo inteiro e depois split('\n'). Os arquivos sao de escopo limitado (Brasileirao + Copa por season, alguns MB), irrelevante para os limites de RAM do Cloud Run. Micro-otimizacao opcional.

**Recomendação:** Para arquivos potencialmente grandes, usar streaming via blob.open('r') iterando linha a linha. Manter full-download onde os arquivos sao comprovadamente pequenos.

### 77. bq.get_table chamado duas vezes por tabela (parity + sync)

- **Arquivo:** `src/sync/bq_to_postgres.py:135-137, 265`
- **Categoria:** performance  ·  **Confiança:** high  ·  **Tema:** Performance e eficiencia

**Problema:** check_schema_parity chama bq.get_table por tabela e _sync_one_table chama de novo para a mesma tabela. Para ~15 tabelas sao ~30 round-trips sequenciais de metadados (gratuitos, so latencia). Impacto desprezivel; gargalo e o list_rows+COPY. Cachear .modified seria ate questionavel para corretude do skip-if-unchanged.

**Recomendação:** Opcional: cachear o objeto Table do pre-flight e reutilizar, ciente de que .modified deve ser o mais fresco possivel antes do TRUNCATE.

---

## Quick wins (baixo esforço, alto valor)

- Incluir 't15m' no whitelist de get_gcs_path (src/config.py:365) — idealmente trocar por phase = f"_{mode}" if mode else "" no branch futebol — e corrigir os comentarios/descriptions/docstrings de DDL que ainda citam '2 janelas' de odds.
- Aplicar teto ao wait do Retry-After em base_client.py (min(wait, 120)) para evitar sleep de horas, e estender o retry para status >= 500 e Timeout/ConnectionError.
- Copiar o dict em get_paginated (params = dict(params or {})) para eliminar a mutacao do argumento do chamador.
- Adicionar timeout=30 ao smtplib.SMTP_SSL em daily_summary.py e notify_execution, e envolver collect_from_logging em try/except no resumo diario.
- Migrar BALLDONTLIE_KEY e API_FOOTBALL_KEY para --set-secrets (Secret Manager) no deploy_cloud_run.sh, ja que src.config le ambas via os.getenv (sem mudanca de codigo).
- Adicionar um switch antes da phase3 dos workflows (NBA e futebol) para pular o sync PRD quando o dbt (dbt-nba/dbt-futebol) estiver em failed_services.
- Remover blob.exists() pos-upload (gcs_storage.py) e a re-validacao json.loads do NDJSON — wins de performance sem risco.
- Reescrever a secao de uso do README (sem flags CLI, sem full_load.py) e adicionar futebol/sync/daily_summary, ou no minimo apontar para o bloco 'Common commands' do CLAUDE.md.
- Atualizar requests para >=2.32.3 em todos os requirements.txt (idealmente via requirements-common.txt centralizado).
- Trocar datetime.utcnow() por datetime.now(timezone.utc) e centralizar um helper now_utc() para padronizar timezone entre NBA e futebol.

---

## Cobertura e limites

Bem coberto: o nucleo do pipeline foi revisado em profundidade por 19 lentes (subsistemas + transversais), com verificacao adversarial linha-a-linha. Camadas src/ (config, clients, storage, bigquery, sync, reporting, extractors NBA e futebol), os scripts orquestradores, os wrappers Cloud Run, os 9 GCP Workflows, os geradores de DDL e a config de deploy/dependencias tiveram alta cobertura, com forte foco em corretude de dados, tratamento de erros, paginacao, idempotencia, segredos e aderencia ao .cursorrules. Os achados de alta severidade convergiram de multiplos revisores (ex.: o bug t15m e o mascaramento de falhas), o que aumenta a confianca. Areas que merecem revisao manual humana mais profunda: (1) os modelos dbt em si (dbt-nba/dbt-futebol) NAO estao versionados neste repo — a materializacao (table vs incremental), o gate de qualidade interno e o comportamento sob falha parcial nao puderam ser verificados, e impactam diretamente os achados de sync de dados parciais e concorrencia de jobs; (2) o comportamento REAL das APIs externas (formato exato do envelope de quota da API-Football, page-size default da balldontlie v2 em player_props/betting_odds, e se odds NBA de fato paginam) so e confirmavel com trafego real/contrato — os achados high de paginacao e quota tem confidence medium por isso; (3) a configuracao de IAM efetiva das service accounts (se possuem storage.buckets.create, escopo das permissoes) precisa ser auditada no GCP, fora do alcance estatico; (4) validacao de ponta-a-ponta das external tables (especialmente o branch q{period} e a divergencia entre os 3 geradores de DDL) requer inspecao no BigQuery de producao; (5) testes automatizados sao praticamente inexistentes no escopo revisado — recomenda-se adicionar testes unitarios para get_gcs_path (parametrizado por todas as janelas) e para a logica de NULL/empty-string do sync antes de mexer nesses pontos.

---

## Apêndice — Metodologia

Pipeline determinístico de 3 fases sobre 19 unidades de revisão (8 subsistemas `src/`, 5 de scripts/infra, 2 de workflows, 4 lentes transversais):
1. **Review** — cada unidade lida a fundo por um agente sênior, achados calibrados por severidade.
2. **Verify** — cada achado reaberto por um verificador cético independente que tenta refutá-lo no código real (37 refutados, descartados).
3. **Synthesize** — dedup semântico + agrupamento por tema + ordenação por severidade.

Dos 165 achados brutos, 37 foram refutados na verificação adversarial e não constam aqui.
