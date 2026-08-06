# Smartbetting — Data Engineering (Ingestão)

Camada de ingestão do Smartbetting: extrai dados de APIs esportivas (balldontlie e API-Football) para a landing no GCS, servida ao BigQuery e ao dbt downstream. Este glossário fixa o vocabulário canônico das duas verticais e dos padrões de coleta.

## Language

### Produto e apostas

**Vertical**:
Uma das duas linhas de dados esportivos que partilham esta base — NBA e Futebol. Identificada pelo valor `sport` (`nba` | `futebol`).
_Avoid_: esporte, pipeline (ambíguo)

**Player props**:
O produto da vertical NBA — mercados de apostas sobre estatísticas individuais de jogador (pontos, rebotes…), ofertados por vendors.

**Value betting**:
O produto da vertical Futebol — identificar odds ofertadas acima da probabilidade justa.

**Edge**:
Diferença entre a probabilidade justa (de-vig da sharp) e a probabilidade implícita na odd ofertada. Edge positivo é a condição necessária de valor.

**De-vig**:
Remoção da margem da casa de um conjunto de odds para obter probabilidades justas.

**Sharp**:
Casa de referência cujas odds de-vigadas servem de proxy de probabilidade justa. No projeto, a Pinnacle.

**CLV (Closing Line Value)**:
Valor de uma aposta medido contra a linha de fechamento — a última odd antes do kickoff. A janela T-15m existe para capturar essa linha.

**Oportunidade de valor**:
Aposta candidata com edge positivo e corroboração suficiente, pontuada pelo Motor de Score.
_Avoid_: pick, dica

**Motor de Score (de Confiabilidade)**:
Motor downstream (dbt, repo `analytics-engineering`) que pontua oportunidades de valor a partir de odds, predictions e premissas.

**Vendor**:
Operador de apostas na vertical NBA (DraftKings, Caesars, BetRivers); cada vendor tem coleta própria de player props.
_Avoid_: casa (na NBA)

**Casa (bookmaker)**:
Operador de apostas na vertical Futebol (Pinnacle, Bet365…).
_Avoid_: vendor (no futebol)

### Competições e partidas

**Liga**:
Entidade da API-Football identificada por `league_id`, com temporadas e flags de coverage. A unidade de parametrização do futebol é a tupla `(league_id, season)`.
_Avoid_: campeonato (informal)

**Competição (`competition`)**:
Slug de produto que separa contexto nas tabelas de fato do futebol (`brasileirao`, `copa_mundo`, …), derivado do `league_id`.

**Temporada (season)**:
Ano de referência da coleta. NBA: ano de início da temporada. Futebol: ano-calendário (Brasil) ou temporada cruzada (Europa).

**Coverage**:
Flags por (liga, temporada) que declaram quais dados a API-Football fornece (odds, injuries, xG…); gate para ligar endpoints por liga. O flag `odds` é sazonal — só fica TRUE com jogos na janela pré-jogo, não é limitação estrutural.

**Game**:
Partida da vertical NBA (balldontlie), identificada por `game_id`.
_Avoid_: fixture (na NBA)

**Fixture**:
Partida da vertical Futebol (API-Football), identificada por `fixture_id`. É a "tabela mãe" da vertical — destrava todos os dados per-fixture.
_Avoid_: game (no futebol), jogo/partida (em texto técnico)

**Kickoff**:
Horário de início da fixture; âncora de todas as janelas pré-jogo.

**Finalizado**:
Fixture em status FT, AET ou PEN — o padrão do projeto para "jogo terminado" inclui prorrogação e pênaltis, deliberadamente mais amplo que só FT.
_Avoid_: `status = 'FT'`, encerrado

**NS (not started)**:
Status de fixture futura; o universo dos polls pré-jogo.

**Escalação (lineup)**:
Formação e relacionados de uma fixture, capturada em duas fases: `confirmed` (anunciada ~T-40min; a fonte não publica escalação provável antes disso) e `real` (pós-jogo). São registros do que se sabia em dois momentos diferentes — uma não é versão melhor da outra, e a confirmada é a única evidência pré-apito de quem entra em campo.
_Avoid_: escalação provável (não existe na fonte)

**Desfalque**:
Jogador indisponível para a partida (lesão ou suspensão) — o que a coleta de injuries captura. A fonte publica a lista por fixture a cerca de dois a três dias do kickoff e não antes; consultar mais cedo devolve vazio, que é ausência de publicação e não ausência de desfalque.
_Avoid_: lesão (mais restrito que desfalque)

**Predictions (baseline da API)**:
Previsão pré-jogo do algoritmo da própria API-Football, coletada como baseline de comparação para um futuro modelo próprio. Não é produto.

**xG (expected goals)**:
Métrica de gols esperados presente nas estatísticas de fixture; insumo do Motor de Score. Cobertura quase total no Brasileirão e nas top-5 europeias, rala nas copas e na Série B — Copa do Brasil é o extremo. Onde é rala, a premissa fica **sem insumo**, que é diferente de sinal fraco.

### Coleta

**Extractor**:
Unidade de extração de um endpoint: busca na API, carimba metadados e grava na landing. Um por endpoint, seguindo um dos arquétipos de coleta.

**Modo**:
Regime de execução da coleta de futebol: `current` = temporada corrente (diário), `backfill` = temporadas passadas (manual, one-off), `pregame` = poll pré-jogo.

**Latest-only**:
Arquétipo em que cada execução sobrescreve um único arquivo — sem histórico. Usado em catálogos e estados correntes.

**Snapshot (diário)**:
Arquétipo com arquivo carimbado por data (`snapshot_date`): a landing acumula um retrato por dia e re-execução no mesmo dia sobrescreve (idempotente). Ex.: standings, injuries.
_Avoid_: confundir com latest-only

**Poll**:
Verificação recorrente (~15min) das fixtures NS futuras, bucketando cada uma pela proximidade do kickoff.

**Janela**:
Banda de proximidade do kickoff que dispara uma captura. As bandas de um mesmo endpoint são **disjuntas** — bandas que se sobrepõem fazem uma passada do poll capturar o mesmo jogo duas vezes, com dois rótulos. T-15m é a janela de fechamento (CLV).

**Janela diária**:
Janela larga, carimbada por data, que varre todo o horizonte futuro com uma captura por dia — em vez de uma foto perto do kickoff. É como se cobre o que está a dias de distância sem multiplicar chamada por poll.

**Horizonte**:
Até quando à frente do kickoff um endpoint é consultado. É escolha nossa, e não deve ser confundida com o que a fonte oferece: quando os dois divergem, é a nossa banda que corta.

**Vazio registrado**:
Registro de que a fonte foi consultada e não tinha o dado. Sem ele, "perguntamos e não veio" e "nunca perguntamos" são o mesmo estado na landing — o skip-if-exists não trava, o poll repergunta o vazio até o kickoff, e a jusante não há como distinguir ausência do mundo de ausência da coleta.
_Avoid_: tratar ausência de arquivo como ausência de dado

**Forward-only**:
Dado que só existe no seu momento: janela perdida não se reconstrói — sem backfill possível. Vale para odds, predictions e escalações confirmadas.

**Skip-if-exists**:
Idempotência por item: o que já está na landing não é re-buscado, exceto dentro da janela de re-fetch.

**Janela de re-fetch**:
Faixa recente em que itens já coletados são re-buscados para capturar correções pós-jogo da API.

**Quota**:
Orçamento diário de requests da API. Estouro na API-Football chega como resposta OK com `errors` preenchido — o run deve falhar, nunca registrar coleta parcial como sucesso. Consumo e data de vencimento do plano são estado observável (`/status`) e pertencem ao resumo diário: cota é insumo de coleta como qualquer outro, e o primeiro sintoma de ter acabado é o produto vazio.

### Plataforma

**Landing**:
Camada de arquivos brutos no GCS — destino de todo extractor, fonte das external tables e memória do que já foi coletado.
_Avoid_: data lake, staging (staging é camada dbt)

**External table**:
Tabela BigQuery `raw_*` que lê os arquivos da landing in place, sem carga: novos arquivos aparecem sozinhos, ao custo de full scan por consulta.

**Mart**:
Tabela final do dbt (repo `analytics-engineering`) pronta para consumo; é o que o sync materializa no Postgres.

**Sync**:
Materialização das marts do BigQuery no Postgres de serving (Supabase), por esporte e ambiente (PRD/DEV).

**Gate**:
Condição que corta etapas downstream quando não há novidade ou o passo anterior falhou (ex.: dbt só roda se algo foi salvo; sync PRD só roda se o dbt passou).

**PARTIAL_FAILURE**:
Desfecho de workflow em que serviços falharam mas a execução termina "SUCCEEDED" no GCP. A detecção real é pelo resumo diário (1 e-mail/dia) e pelos logs de WARNING — nunca pelo status do workflow.
