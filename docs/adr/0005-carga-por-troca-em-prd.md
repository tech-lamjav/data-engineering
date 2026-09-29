# Em PRD, as tabelas do futebol são carregadas por tabela-sombra com troca, não por TRUNCATE + COPY

**Status:** accepted (2026-09-29)
**Issue:** [DE #108](https://github.com/tech-lamjav/data-engineering/issues/108)

## Contexto

O sync carregava cada tabela no lugar: `TRUNCATE` e `COPY` na mesma transação. O `TRUNCATE` pega
ACCESS EXCLUSIVE e só solta no commit, ao fim do COPY, então **toda leitura do app na tabela fica
bloqueada** e o PostgREST a cancela por `statement_timeout` (3 s para `anon`, 8 s para
`authenticated`). O docstring do módulo dizia que o leitor via o dado antigo durante a carga; isso
era falso.

O tempo de lock, medido em 29/09/2026, é na maior parte **tempo de leitura do BigQuery**, não de
escrita: o `TRUNCATE` sai antes de o `list_rows` devolver a primeira linha. `fact_fixtures`
(~10,9 mil linhas) leva 25–64 s só para ler (leitura isolada na máquina local, coerente com os
22–75 s de lock dos logs de PRD), e a leitura explica ~85% da mediana de 568 s de
`fact_odds_snapshot` (estimativa, extrapolada da mesma medição local). Toda tabela grande bloqueia o
app, com ou sem concorrência.

## Decisão

Em **PRD**, a tabela é carregada numa **tabela-sombra** e trocada pela vigente numa transação de
milissegundos. A troca é habilitável por ambiente e por tabela: em DEV só as tabelas pequenas do
canário a usam. O caminho antigo (carga no lugar) continua existindo e testado, e vale para o resto
de DEV (que já está acima do teto de disco) e para o NBA.

1. **A estratégia é por tabela, não uniforme.** Uma preflight genérica olha os dependentes da
   tabela vigente por OID (views, funções com o tipo da tabela na assinatura, sequências). Tabela
   com dependente **não** usa a troca: cai no caminho staged (COPY para tabela temporária e,
   numa transação curta, `TRUNCATE` + `INSERT … SELECT`) ou no caminho antigo, com WARNING e
   contador no resumo diário. Hoje isso vale para as cinco `int_futebol_premissas_*`, que a view
   `vw_premissas_acesas` lê.
2. **A sombra é derivada da tabela viva**, nunca definida pelo sync. O DDL das tabelas continua
   sendo do app. Como a cópia de formato do Postgres não leva RLS, políticas, permissões, dono nem o
   comentário da tabela, esses são relidos do catálogo da tabela vigente e reaplicados. Os índices
   são criados com nome explícito a partir da definição dos índices da vigente.
3. **A troca confere o formato sob lock.** Já com o lock da tabela vigente, um fingerprint (colunas,
   tipos, ordem, NOT NULL, defaults, índices, CHECKs, comentários, permissões, RLS e políticas, opções
   de armazenamento, identidade de réplica) compara vigente e sombra. Se divergir, a troca aborta, a sombra é descartada e o
   próximo ciclo a recria: uma migration do app que caiu durante a carga não se perde em silêncio.
4. **O teto de espera é curto, com retentativas** (números na spec da DE#108): o leitor que chega
   durante a espera fica na fila por no máximo o teto. Esgotadas as tentativas, a tabela **falha
   alto**, a execução devolve status parcial com o nome da tabela e o estado de sincronização não
   avança (o detector de atraso cobre).
5. **A sombra é commitada antes da troca** (a tentativa seguinte a reaproveita) e **toda sombra órfã
   é removida no início de cada execução**, com a trava da DE#107 em mãos.
6. **Lançamento por tabela, com rollback por workflow** (a imagem entra com a troca desligada).
   `fact_odds_snapshot` só entra na troca depois da DE#109.

## Alternativas consideradas

**Só o caminho staged para todas as tabelas.** Carrega fora do lock e trava só durante o
`INSERT … SELECT`. É simples, mas o tempo de lock cresce com o tamanho da tabela (~1,2 s para 600 mil
linhas num Postgres local; nas tabelas grandes de PRD seria de segundos a dezenas de segundos, contra
milissegundos da troca). Fica como fallback para tabelas com dependente.

**`DELETE` + `COPY` sem `TRUNCATE`.** Mantém o leitor servido por MVCC, mas gera bloat e vacuum,
que pesam no DEV free e não resolvem o tempo de leitura do BigQuery.

**Carga incremental.** Não resolve o lock das tabelas que não são append-only e a `fact_odds_snapshot`
tem uma janela diária mutável (a `daily`); ver ADR 0006.

## Consequências

- **Cada troca é DDL.** Os event triggers do Supabase disparam `NOTIFY pgrst` a cada
  `CREATE`/`ALTER`/`DROP`, inclusive em schema não exposto. O efeito de uma rajada (503 no
  `edge_logs`) só se mede no DEV, e é critério de aceite do canário.
- **Contadores de uso por tabela** (`pg_stat_user_tables`) recomeçam do zero a cada troca; não são
  fonte confiável de "quem lê" depois do cutover.
- **Nomes dos índices** precisam voltar aos canônicos depois do `DROP` da tabela velha: migrations do
  app criam índice por nome com `IF NOT EXISTS`.
- **Um dependente novo criado pelo app** (uma view sobre uma tabela hoje sem dependente) faz a
  tabela cair no fallback, com WARNING, em vez de derrubá-la: quem cria a view deve saber disso.
- **Disco e WAL (inferência, não medida):** o `TRUNCATE` atual já cria um arquivo novo e mantém o
  velho até o commit, então o pico de disco deve ser parecido; criar os índices depois do `COPY`
  escreve WAL de índice. Antes de ligar `fact_odds_snapshot`, conferir a folga de disco do PRD.
- **O que só DEV e PRD provam:** o comportamento do Supavisor em modo sessão e do PostgREST diante do
  `NOTIFY`. O plano em cache do plpgsql e a fila de locks foram provados num Postgres 17 local.
