# Detector de atraso do sync, fora do GCP e medindo atraso em vez de idade

**Status:** accepted (2026-08-29)
**Irmão de:** `docs/adr/0001-carimbo-de-procedencia-dos-servicos-cloud-run.md`
**Issue:** [DE #62](https://github.com/tech-lamjav/data-engineering/issues/62)

O sync BigQuery → Postgres é o único caminho pelo qual o dado chega ao app. Quando ele para, o
serving **não quebra** — as RPCs seguem respondendo, com o dado da última sincronização
bem-sucedida. É uma falha que não se anuncia sozinha para quem lê, e por isso precisa de um
vigia. Este ADR registra as duas decisões que um leitor futuro questionaria: por que o vigia
mora no GitHub Actions em vez do GCP, e por que ele mede *atraso* e não *idade*.

## A evidência que motivou

De 26/08 18:00 UTC a 29/08 15:00 UTC — **3 dias, ~72 execuções** — o `workflow-futebol-sync`
abortou de hora em hora em PRD e DEV. O PR #122 do `analytics-engineering` removeu duas colunas
de `int_futebol_premissas_ou`; o Postgres continuou com elas; o parity check pré-flight abortou
o sync inteiro antes de qualquer TRUNCATE. Nenhum dado foi corrompido e nada quebrou na cara do
usuário — o board simplesmente parou de mudar.

O alarme existia e **funcionou**: o resumo diário mandou `[FALHAS]` nos dias 27, 28 e 29.
Ninguém agiu. Esse é o fato que governa este ADR: o problema não era detecção ausente, era
detecção indistinguível do ruído de fundo.

Custo secundário, medido em 29/08: a recuperação de um atraso longo estoura o timeout de 900s do
Cloud Run (dois 504 com latência exata de 900s) e precisa de 2–3 passadas. **Quanto mais tarde se
detecta, mais cara é a volta.**

## Vocabulário

- **Detector** — vigia que roda **fora** do sistema que observa. Distinto de **guarda** (teste
  dbt com `tag:guarda`), que roda de dentro da mesma imagem que protege e por isso não enxerga a
  classe de falha que apaga a si mesma. A distinção já existia de fato no
  `analytics-engineering` (o `deriva-imagem.yml` se autodenomina detector e explica por quê);
  este ADR a promove a termo, e ela está no `CONTEXT.md`.
- **Atraso do sync** — há quanto tempo o BigQuery está à frente do Postgres, por tabela. Zero
  quando não há nada pendente, independentemente de quando o sync rodou pela última vez.

## Decisão A — o detector roda no GitHub Actions, não em Cloud Run + Scheduler

O detector existe para enxergar o sync parado, e o sync é um Cloud Run disparado por Workflow,
agendado por Cloud Scheduler. Hospedá-lo na mesma infra o faria morrer junto com ela: um Cloud
Scheduler pausado, um Workflow que não dispara ou um projeto com problema de IAM apagam o
sintoma **e** o vigia.

É a mesma lição do ADR 0001 do `analytics-engineering`, que documenta o caso concreto: em
07/08/2026 a fase de guardas do futebol passou com TOTAL=6 em vez de 13 porque a imagem estava
velha — a guarda que existia para pegar o defeito não rodou, e só um detector fora da imagem viu.

**Alternativas descartadas:**

- **Cloud Run + Cloud Scheduler**, como todo o resto do repo. Consistente com a casa, e errado
  pela razão acima. Consistência não vale a propriedade que faz o vigia ser um vigia.
- **Mais uma seção no resumo diário.** Custo quase zero, e duas falhas: cadência de 1×/dia é
  exatamente a que já falhou, e o resumo roda na mesma infra que ele estaria vigiando.

**Custo aceito:** este é o primeiro `.github/workflows/` do repositório, e a credencial de banco
passa a existir como secret de um repositório **público**. Mitigado pela decisão C.

## Decisão B — o sinal é atraso (BQ à frente do PG), não idade da sincronização

A formulação óbvia — "esta tabela não é sincronizada há mais de N horas" — exige um N por tabela,
porque as cadências vão de 15 minutos (odds) a uma semana (`fact_team_season_stats`). Uma tabela
semanal ficaria vermelha 6 dias em 7, e a tabela de limiares precisaria ser mantida em sincronia
com o calendário de cada workflow — mais um registro à mão para derivar.

O atraso normaliza isso sem configuração: se o BQ não mudou, não há o que sincronizar e o atraso
é zero, qualquer que seja a cadência. **Um limiar global, não 22.** E o dado já existe: o
`_sync_state` guarda `last_synced_bq_modified_time`, que é exatamente o carimbo que o
skip-if-unchanged compara.

O limiar é **3 horas** — 3 ciclos do sync horário. Não é mais apertado por causa do custo de
recuperação medido acima: um limiar de 1h acenderia no meio de uma recuperação legítima em
andamento.

**Alternativa descartada:** `max(dbt_loaded_at)` da tabela no Postgres. Mede a idade do dado, não
a pendência, e recai no mesmo problema do limiar por tabela.

## Decisão C — o e-mail sai do detector, e só em transição de estado

A notificação nativa do Actions não serve como canal aqui, por dois fatos verificados no
`analytics-engineering`: ela vai **apenas para quem criou o workflow** (o time não recebe nada),
e produz uma notificação **por execução** — 104 falhas consecutivas geraram "cem e poucos"
e-mails em agosto.

Então o detector manda o próprio e-mail, pelos mesmos secrets do resumo diário, e **só quando o
estado muda**: verde→vermelho abre o episódio, vermelho→verde informa a recuperação e quanto
tempo durou, e o vermelho continuado fica calado com um lembrete a cada 24h. O estado mora em
`futebol._detector_state`, no próprio Postgres que o detector já acessa.

Isto é o que ataca a causa real dos 3 dias. Um alarme que chega todos os dias é indistinguível
do estado normal; um que chega quando algo **muda** carrega informação.

O papel de banco é dedicado (`detector_atraso`): `select` em `_sync_state`, `select/insert/update`
apenas em `_detector_state`. Não há motivo para a URL de escrita de produção existir como secret
de repositório público. O fallback para a URL de escrita, em `get_pg_url_ro`, é conveniência
local e emite warning — um degrau de privilégio silencioso seria pior que o inconveniente.

## Decisão D — o detector não conserta nada

Ele poderia re-disparar o sync ao detectar atraso. Não faz, por duas razões: o agendamento
horário já é o retry (em 29/08 o sync se resolveu sozinho porque commita por tabela), e
re-disparar empilha execução concorrente — que foi exatamente o que produziu o `PARTIAL_FAILURE`
das 15:00 daquele dia, quando um disparo manual correu junto com o agendado. Detector que age
vira causa.

## Consequências

- O repositório passa a ter CI. Antes não tinha nenhum.
- **Pré-requisitos que precisam existir ANTES de o workflow entrar no master**, sob pena de o job
  falhar de hora em hora:
  1. `scripts/sql/detector_atraso.sql` rodado em PRD **e** DEV (cria `futebol._detector_state` e o
     papel `detector_atraso`). Ao contrário do `_sync_state`, esta tabela **não** é auto-criada:
     o papel do detector não tem CREATE, e dar DDL a ele anularia a razão de o papel existir.
  2. Os cinco secrets listados no cabeçalho do workflow.
- O código de saída do script é 1 apenas nos ciclos que **falam** (transição ou lembrete), não em
  todo ciclo vermelho. Falhar de hora em hora enquanto o episódio dura reintroduziria pela porta
  dos fundos a inundação que a decisão C recusa — o GitHub notifica por execução. O custo aceito é
  que a aba Actions fica verde durante um episódio já anunciado; o estado vivo está no e-mail e no
  log, e um episódio ainda produz um job vermelho por dia.
- Falha ao medir UMA tabela (sumiu do BQ, renomeada, IAM) vira linha vermelha no relatório, não
  exceção: o detector precisa sobreviver a mexidas no pipeline, que é justamente quando ele mais
  importa.
- O detector cobre **qualquer** causa de serving parado, não só deriva de schema: incidente de
  pooler, OOM, timeout, abort silencioso. Todas já aconteceram neste sync.
- O que ele **não** cobre: dado que chega fresco e **errado**. Mudança de grão passa o parity e
  passa o detector — continua sendo trabalho do contrato de serving e da revisão humana.
