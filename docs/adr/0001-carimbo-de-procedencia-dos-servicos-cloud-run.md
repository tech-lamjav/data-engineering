# Carimbo de procedência dos serviços Cloud Run

**Status:** accepted (2026-08-24)
**Estende:** `analytics-engineering/docs/adr/0001-carimbo-de-procedencia-da-imagem-dbt.md`
**Issue:** [DE #44](https://github.com/tech-lamjav/data-engineering/issues/44)

Os 29 serviços Cloud Run deste repo rodam de uma revisão pré-buildada, não do master. Quando
alguém mergeia e não redeploya, produção segue rodando código velho — e **nada acusa**. O ADR
0001 do `analytics-engineering` resolveu essa classe para os dois Cloud Run **Jobs** dbt, com um
carimbo de procedência conferido de hora em hora por um detector que roda fora da imagem. Este
ADR estende o mesmo mecanismo aos **Services**, e registra as decisões em que os dois casos
**divergem** — que são mais do que parecia.

Este ADR mora no `data-engineering` e não junto do 0001 porque o **escritor do carimbo é o
`scripts/deploy_cloud_run.sh`**, que é deste repo (ver decisão B). Um ADR cujo dono não pode
editá-lo sem PR em outro repo apodrece.

## A evidência que motivou

Os 13 serviços da NBA rodaram de **25/06 a 24/08/2026 — dois meses** — com `src/` defasado,
incluindo a ausência do retry de upload ao GCS (`b0da004`, 31/07). O que expôs isso foi uma
investigação manual, não um alarme. O redeploy dos 13 foi executado em 24/08 (16:54–17:21 UTC,
13/13, zero erro), mas **redeployar não conserta a classe**: o `src/` volta a derivar assim que
alguém tocar no futebol sem tocar na NBA, que é exatamente o que aconteceu entre 31/07 e 07/08.

Medições da árvore, janela de 92 dias (2026-05-24 → 2026-08-24, 104 commits):

| o que | commits | consequência |
|---|---|---|
| tocam `src/` | 52 | derivariam a frota inteira num hash ingênuo |
| tocam o **núcleo compartilhado** (`config.py`, `clients/`, `storage/`, `utils/`, `bigquery/`) | **38** | derivam a frota **de verdade** — a cada ~2,4 dias |
| tocam `scripts/futebol/` | 13 | **não** mudam comportamento de nenhum serviço de NBA |
| tocam `src/extractors/` | 16 | atingem um serviço cada |
| tocam `cloud_run/<svc>/` | 15 | atingem um serviço cada — deriva **individual** existe |
| `src/config.py` sozinho | 29 (28 com código, 1 só comentário) | o núcleo domina a deriva de frota |

## Vocabulário

Nenhum termo novo além de um. **Procedência** ("o que uma revisão em produção declara sobre a
própria origem") e **deriva** ("o que roda em produção não corresponde ao master") já estão
definidos no ADR 0001 e cobrem isto sem esticar.

- **Frota** — os 29 serviços que compartilham o núcleo de `src/`. É a unidade em que a deriva
  compartilhada acontece e em que o remédio (`./deploy_cloud_run.sh`) é aplicado.

Fica aqui e não no `CONTEXT.md` pela mesma regra do ADR 0001: `CONTEXT.md` é glossário de
domínio, e isto é infraestrutura.

## Decisões

### Escopo: 29 alvos, não 26

A issue #44 falava em "26 extractors". A tabela do `deploy_cloud_run.sh` tem **29**: 13 NBA + 13
futebol + 3 compartilhados (`notify-execution`, `sync-bq-to-postgres`, `daily-summary`).

Os três compartilhados entram, e não por simetria: **`daily-summary` é o canal de alarme**. Um
`daily-summary` derivado é o caso em que o detector fica vermelho e o e-mail que deveria contar
isso roda código velho que nem sabe ler a seção nova — "o detector apagado pela mesma causa que
o bug", a frase que abre o ADR 0001.

### A. Carimbo combinado **por serviço**, com os componentes gravados à parte

Rejeitado o alvo sintético único (`src_compartilhado`) que a #44 sugeria como saída para a
explosão de 29 linhas: ele é **semanticamente errado**. Um serviço deriva sozinho quando só o
`main.py` dele muda — 15 commits em `cloud_run/` no período —, e um alvo compartilhado é cego
para isso. O carimbo por serviço teria de existir de qualquer forma, e o sintético viraria um
segundo mecanismo cobrindo um subconjunto do primeiro.

O deploy grava quatro env vars por serviço:

```
PROCEDENCIA_HASH         hash combinado  <- a verdade: "este serviço está em dia?"
PROCEDENCIA_HASH_NUCLEO  hash do núcleo compartilhado
PROCEDENCIA_HASH_SVC     hash do que é só deste serviço
PROCEDENCIA_SHA          commit (já lido por src/reporting/procedencia.py)
```

O agrupamento que a #44 pede vira **apresentação, não estrutura**. Verdade por serviço, ruído
por causa: quando os 29 divergem no mesmo `_NUCLEO`, o relatório imprime **uma** linha.

### A′. O hash cobre o que o serviço **executa**, não o que o deploy **copia**

O `deploy_cloud_run.sh` copia `src/` e `scripts/` **inteiros** para dentro de cada imagem. Um
hash do que é copiado poria os 13 serviços de NBA em deriva a cada commit em
`scripts/futebol/` — 13 no período —, que é exatamente a classe de alarme falso que a decisão 2
do ADR 0001 rejeitou. Alarme cruzado entre verticais treina o time a ignorar o detector.

Manifesto de três blocos, por serviço:

| bloco | conteúdo | alcance |
|---|---|---|
| **núcleo** | `src/config.py`, `src/clients/`, `src/storage/`, `src/utils/`, `src/bigquery/`, `cloud_run/<dir>/requirements.txt` | todos os 29 |
| **módulo** | `src/extractors/<x>_extractor.py`; `scripts/<x>.py` **só para NBA**; `src/reporting/` (daily-summary); `src/sync/` (sync-bq-to-postgres) | um serviço |
| **serviço** | `cloud_run/<dir>/main.py`, `Procfile` quando existir | um serviço |

O `scripts/` é comportamental **apenas para os 13 serviços de NBA**: eles fazem
`sys.path.insert(0, scripts_dir)` e importam `from extract_games import main`, que resolve em
`scripts/extract_games.py`. Os 13 de futebol e o `daily-summary` inserem o path mas importam de
`src.*` — para eles, `scripts/` está na imagem e não é executado.

Ganho medido: a deriva de frota cai de 52 para **38 eventos/92d** (−27%), e **100%** dos
alarmes cruzados futebol↔NBA desaparecem. O núcleo continua dominando, e isso é a verdade —
não um defeito do desenho.

⚠️ **O manifesto declarativo apodrece para dentro.** Alguém acrescenta um import, esquece de
declarar, o hash passa a cobrir menos e o detector emudece — **fail-open**, o pecado capital
deste mecanismo. Remédio: um teste em CI que calcula o **fecho de imports** por AST a partir do
`main.py` de cada serviço e **falha** se o fecho alcançar um módulo não declarado. A declaração
continua sendo a verdade; o fecho é o auditor que impede o encolhimento silencioso. (O fecho
não vira a fonte do hash porque um parser que erra um import encolhe a cobertura em silêncio,
enquanto um manifesto com path errada falha alto — a mesma escolha fail-closed da decisão 7 do
ADR 0001.)

### B. O hasher e o detector moram **neste** repo

A #44 listava três saídas. A restrição que decide não é path — é **versionamento acoplado**:
quem escreve o carimbo (`deploy_cloud_run.sh`, aqui) e quem o confere têm de mudar a função de
hash e o manifesto **no mesmo commit**. A decisão 2b do ADR 0001 já documenta que essa mudança
não é retrocompatível e que o fix precisa estar no master antes do rebuild. Com o hasher em
outro repo, essa ordenação passa a atravessar dois repos e dois PRs, e nenhum PR daqui pode ser
barrado por incoerência com um script de lá.

Arquivos, com dono definido:

- `scripts/procedencia_servicos.sh` — calcula o hash de um serviço (**deste** repo)
- `scripts/checa_deriva_servicos.sh` — confere os 29 contra o Cloud Run (**deste** repo)
- o `analytics-engineering` segue dono do `procedencia.sh`/`checa_deriva.sh` dos jobs dbt

**Rejeitado — o detector do AE aceitar uma raiz externa por parâmetro.** Um `checa_deriva.sh`
que hasheia a árvore de um checkout vizinho é a mesma fragilidade que este projeto já registrou
(o `checa_deriva.sh` mente quando rodado de dentro de um worktree), e o CI do AE passaria a
depender de um checkout do DE para uma coisa que não é dele.

**Rejeitado por ora — extrair o `procedencia.sh` para um pacote comum.** É o mais limpo e o
mais caro; não existe hoje pacote comum entre os repos. A duplicação real são ~40 linhas de
hash de blob, e ela não paga um pacote.

Custo assumido conscientemente: a lógica de hash existe em duas cópias e pode divergir.
Mitigação verificável — as duas emitem `<path> <blob>` ordenado com `LC_ALL=C`, e um teste em
cada repo fixa o formato.

### C. Workflow agendado **separado**, e não uma matrix no detector existente

O detector dos serviços vai para `.github/workflows/deriva-servicos.yml`, **deste** repo — não
como um alvo novo na matrix do `deriva-imagem.yml` do AE.

O cabeçalho do `deriva-imagem.yml` já pagou por essa lição em 104 execuções vermelhas seguidas:
**o GitHub notifica por execução, não por job**. A matrix separa o diagnóstico, não o estado da
notificação. Um alvo que fica vermelho a cada ~2,4 dias, na mesma execução dos jobs dbt,
**anestesiaria o `dbt_futebol`**, que é o alvo caro. Arquivos separados é a correção completa
que aquele cabeçalho já nomeia como pendente; aqui ela deixa de ser opcional.

Consequências operacionais, as duas pequenas: este repo ganha `.github/workflows` do zero e
precisa do secret de service account nas settings **uma vez** — e a SA precisa de
`roles/run.viewer` **em serviços**, não só em jobs. O desativamento de workflow agendado após 60
dias sem commit em repo público não é risco aqui: 104 commits em 92 dias.

### C′. O sinal chega ao resumo diário pelo **mesmo token `[DERIVA]`**

O `src/reporting/procedencia.py` já lê o veredito do detector pela API pública do GitHub e já
converte isso no token `[DERIVA]` do assunto. O par `(REPO_DETECTOR, WORKFLOW_DETECTOR)` vira
uma **lista** de pares, e `ProcedenciaInfo.alarme` vira o OR dos vereditos. Repo público ⇒ GET
sem token, igual ao de hoje.

**Token novo foi rejeitado.** A ação do operador é a mesma classe — "produção não corresponde ao
master" —; o corpo do e-mail diz qual frota. Token novo é vocabulário novo para o mesmo
conceito, e o ADR 0001 gastou uma seção explicando por que não se multiplica palavra à toa.

⚠️ **Bootstrap:** mudar o `procedencia.py` exige redeployar o `daily-summary`, que é ele próprio
um alvo ainda não coberto. A ordenação é **workflow e carimbo primeiro, imagem/serviço depois**
— o inverso deixa o detector vermelho sobre um serviço que ainda não sabe se carimbar.

### Cinco estados, e o quinto é novo

Estende a taxonomia do `checa_deriva.sh`, que já separa "carimbo ausente" de "erro de leitura":

1. **em dia** — hash bate
2. **em deriva** — hash diverge, com a causa (núcleo / módulo / serviço)
3. **sem carimbo** — o serviço existe e não tem `PROCEDENCIA_HASH`. Distinto, **fail-closed**
   (conta como deriva), com outro remédio na tela
4. **erro de leitura** — API/IAM falhou. Não é deriva; é o detector não sabendo
5. **órfão** — passo de reconciliação: `gcloud run services list` contra o manifesto. Serviço
   **vivo e não declarado**, ou entrada declarada **nunca deployada**. Ambos vermelhos, com
   textos diferentes

O estado 5 fecha a armadilha de segunda ordem: um detector cujo universo é uma lista escrita à
mão é cego para tudo que não está na lista, e essa cegueira não faz barulho nenhum. O próprio
`deploy_cloud_run.sh` já pede, em comentário, que ninguém acrescente `extract_player_props` à
tabela "sem antes confirmar que não há serviço Cloud Run órfão rodando defasado em produção" —
hoje essa confirmação não existe em lugar nenhum.

### O carimbo é escrito pelo próprio `gcloud run deploy`, e conferido de volta

Decisão 6 do ADR 0001 aplicada aqui: foi *o segundo comando manual* que deixou o fix do de-vig
2 dias fora de produção. O carimbo entra no **mesmo** `gcloud run deploy`, por `--set-env-vars`.

- `--set-env-vars` e não `--update-env-vars` — o oposto do job dbt, e de propósito: no serviço
  **o deploy é o único escritor** e já passa o conjunto completo. Aqui `--set` é o correto.
- As **quatro** ramificações do `deploy_service()` (`notify-execution`, `sync-bq-to-postgres`,
  `daily-summary`, padrão) montam env vars independentes — é onde se esquece uma. Uma função
  `carimbo_env_vars <svc>` concatenada nas quatro.
- **Leitura de volta pós-deploy**: se `PROCEDENCIA_HASH` não voltar do `services describe`, o
  deploy **falha**. Carimbo que o próprio deploy não confirma é carimbo que some na primeira
  refatoração.

### O remédio continua manual (com uma alternativa registrada)

Detector + `./deploy_cloud_run.sh` à mão, coerente com as decisões 8 (sem janela de graça) e 10
(o alarme não bloqueia merge) do ADR 0001.

⚠️ **Registrado porque a evidência puxa para o outro lado:** a frota deriva de verdade a cada
~2,4 dias e o remédio custa ~60 min de build (13 serviços levaram 27 min em 24/08). Isso entrega
um painel **vermelho quase metade dos dias** — a anestesia que o cabeçalho do `deriva-imagem.yml`
descreve como modo de morte de detector. Se incomodar, a saída **não** é afrouxar o detector: é
um **redeploy noturno agendado da frota**, que faz do verde o padrão e mantém o humano fora do
caminho crítico sem pôr o master em produção instantaneamente. Contradiz a rejeição de
auto-deploy do ADR 0001 e por isso não foi adotada agora — fica nomeada para ser reaberta com o
número em mãos, não redescoberta.

## O que fica FORA de cobertura

Nomeado para ninguém confundir com cobertura:

1. **Config vinda do `.env` do operador** (`SEASON`, `GCS_BUCKET_NAME`, `LOG_LEVEL`) — muda
   comportamento e é **invisível** a qualquer hash de conteúdo git. Um `SEASON` errado passa verde.
2. **Secrets do Secret Manager** — `:latest` resolve em runtime; rotação não muda carimbo nenhum.
3. **O builder do buildpacks.** `--source` sem Dockerfile: o Google atualiza a imagem-base por
   baixo. **Carimbo igual não significa imagem idêntica** — significa "o código é o do master".
   É a mesma promessa dos jobs dbt, e é a promessa certa; só não pode ser vendida como outra.

## Como isto se falsifica

Ver verde num dia calmo não prova nada — foi assim que a fase de guardas passou com `TOTAL=6`.
As três falsificações exercitam caminhos de código diferentes e todas rodam sem build:

```
# 1. deriva (estado 2)
gcloud run services update extract-games --region us-east1 \
  --update-env-vars PROCEDENCIA_HASH=deadbeef
./scripts/checa_deriva_servicos.sh extract-games      # tem de sair VERMELHO
gcloud run services update extract-games --region us-east1 \
  --update-env-vars PROCEDENCIA_HASH=<valor real>     # desfaz

# 2. carimbo ausente (estado 3)
gcloud run services update extract-games --region us-east1 \
  --remove-env-vars PROCEDENCIA_HASH                  # vermelho, com OUTRO texto

# 3. órfão (estado 5)
#    deployar um serviço fora do manifesto e conferir que a reconciliação o acusa
```

Custo: ~20 segundos cada, reversível.

## Consequências

- Enquanto um serviço não for redeployado com o script novo, ele aparece **vermelho** — é o
  fail-closed funcionando, não um defeito. Os 29 nascem vermelhos.
- O painel dos serviços será vermelho com frequência. Isso é a medição, não o detector: a frota
  **está** derivada a cada ~2,4 dias. Ver a alternativa do redeploy noturno acima antes de
  concluir que o detector é barulhento demais.
- `analyses`/docs não têm análogo aqui: `cloud_run/<dir>/` só contém `main.py`,
  `requirements.txt` e às vezes `Procfile` — os três comportamentais.
- O `procedencia.py` passa a ter dois vereditos; se um dos dois repos ficar sem detector, o
  campo `error` já existente cobre o caso sem virar alarme.
