# Amistosos de seleção entram como competição de insumo, não de produto

**Status:** accepted (2026-09-21) — decisão 7 revista em 2026-09-23, ver "Medição (DE#95)"
**Issue:** #92 · #93 · #94 · #95 · #96
**Contexto completo:** `docs/SELECOES_NATIONS_LEAGUE_AMISTOSOS.md`

## Contexto

A entrada da UEFA Nations League (league_id 5) expôs um problema medido: das 54 seleções da
competição, **só 16 têm alguma linha em `fact_fixtures`**, todas vindas da Copa do Mundo 2026, e o
jogo mais recente de qualquer uma delas é de **2026-07-19**. As outras 38 estreiam sem forma. Desde
a #91 / ADR 0010 a forma atravessa competição (`pit_escopo: todas`, `pit_recorte: ultimos_10`), então
o histórico existe em princípio; ele só não foi coletado.

Os amistosos de seleção (league_id 10) são a fonte natural desse histórico. Só que essa liga não se
parece com nenhuma das doze já ingeridas:

- **726 fixtures** na season 2026, dos quais **553 já finalizados**.
- **441 times** num único `league_id`: cerca de 177 seleções principais, 198 de base ou femininas, e
  66 clubes, incluindo times da MLS.
- O campo `national` da API **não separa**: São Tomé e Príncipe vem `false`, Albania U21 vem `true`.
- Quatro times que aparecem em fixtures **não existem** no `/teams` da própria liga.

Havia ainda uma premissa errada, que foi falsificada antes da decisão: acreditava-se que a API não
servia odds de amistoso. Ela serve. A cobertura no dia do jogo foi de **3 em 3**, com Pinnacle,
incluindo Dominica x Anguilla. O "zero" observado a três dias do apito era artefato da janela de
retenção de ~7 dias da API somada à ausência de amistosos naquele intervalo. A diferença real é de
**prazo**: amistoso é precificado entre 7h e 46h do apito, contra 3 a 4 dias das competições
oficiais.

## Decisão

Amistosos entram como **competição de insumo**: alimentam o histórico de forma das seleções e
**não geram oportunidade de valor**.

Consequências de desenho que caem dessa escolha:

1. **Coleta essencialmente só `fixtures`.** `int_futebol_team_form_pit` lê apenas `fact_fixtures` e
   `fact_standings_snapshot`, então placar e data bastam. Não entram odds, predictions, tabela,
   desfalques nem os quatro fatos per-fixture pós-jogo. O `PerFixtureExtractor` ganha gate de liga,
   que hoje não tem.

   ⚠️ Duas ressalvas, para o registro não prometer mais do que entrega. A **escalação pré-jogo** é
   um quinto caminho e **não** é coberta por esse gate: `fixture_lineups_extractor` lê
   `get_upcoming_fixture_ids(45)` (`gcs_storage.py:596-644`), que filtra só por status NS e janela de
   kickoff, sem olhar liga. Os 22 jogos futuros do universo puxariam uma chamada cada a T-45min,
   a menos que o gate seja estendido a esse leitor — decisão que a spec fecha. E o **coletor ao vivo**
   do `data-engineering` recolhe a liga automaticamente ao entrar em `FIXTURES_CURRENT`, porque ele
   se ancora no arquivo de fixtures e não numa lista própria; isso é desejável, já que é o que
   assenta o placar final no mesmo dia, e não se confunde com o coletor do Supabase
   (`public.leagues_config`), que fica de fora por não haver aposta a liquidar.
2. **O universo é derivado, não escrito.** Entram apenas os jogos em que **os dois times já são
   conhecidos por outras competições** do pipeline. Isso corta o universo de 726 para **139**
   fixtures (115 finalizados), dispensa interpretar nome de time e exclui base, feminino e clube
   sem nunca mencionar essas categorias. O conjunto é **append-only**: time que entrou nunca sai,
   para que remover uma competição da config não encolha o histórico para trás.
3. **Um filtro só, na extração.** A camada raw deixa de ser cópia fiel da resposta da API. É o preço
   aceito para não manter a mesma regra sincronizada entre Python e SQL — o precedente da regra de
   meia-linha, que viveu em quatro cópias e divergiu, pesou mais que a fidelidade da landing.
4. **O slug `amistosos` entra só em `fact_fixtures`**, e não nos seis marts. Um `WHEN` em
   `fact_odds_snapshot` sugeriria uma cobertura de odds que a decisão 1 recusou.
5. **A liga fica fora de `TEAMS_*` e `PLAYERS_*`**, por exceção nominal no invariante de
   `tests/test_config_ligas_futebol.py`. O catálogo de jogadores da liga 10 pagina em **134 páginas**,
   ou seja 134 chamadas por dia para um dado que a finalidade não consome.

## Alternativas consideradas

**Amistosos como produto.** Viável: existe preço e existe Pinnacle. Recusada porque as bandas de
`FUTEBOL_ODDS_WINDOWS` são **globais, não por liga**, e a banda `daily` (1 a 7 dias) é
estruturalmente vazia para amistoso. Fazer amistoso virar produto exige janela de odds por liga, que
não existe, e criar esse regime na mesma semana em que a Nations League entra acumula duas mudanças
estruturais.

**Filtrar por nome e pela flag `national`.** Recusada: deixa 346 jogos, depende de interpretar nome
de time, e erra nos dois sentidos, conforme os casos de São Tomé e de Albania U21 acima.

**Raw fiel com filtro nos leitores e no dbt.** Recusada pelo motivo 3.

## Consequências

- **Uma guarda reprova no dia 1 se não for tratada no mesmo commit.**
  `assert_per_fixture_coverage_anomala` é `severity='error'`, roda sob `tag:guarda` no workflow
  diário, tolera 10% de buraco e tem blocklist de apenas `['copa_do_brasil', 'champions_league']`
  (`:43`). A decisão 1 garante **0%** de cobertura per-fixture em 115 fixtures finalizados, o que é
  seis vezes a tolerância. `amistosos` precisa entrar nessa lista junto com o slug.

  Isso muda o significado da lista, e vale dizer em voz alta: o critério escrito no teste é
  "mata-mata cujas fases iniciais a API não cobre". Amistosos entram por outro motivo — **nós**
  decidimos não coletar. A lista passa a ter duas razões distintas, e o comentário do teste precisa
  refletir isso ou vira mentira para o próximo leitor.

- **O histórico de jogos já medidos muda.** Os 115 amistosos finalizados entram no histórico PIT de
  seleções que já têm linhas de Copa do Mundo no mart, deslocando a forma calculada para jogos já
  medidos. A ingestão do passado só acontece se o deslocamento couber na régua de **0,25 pontos
  percentuais** herdada da task [F] (#92), e a medição roda contra o target `futebol_taskF`, não
  contra produção.
- **Aparece no app sem oportunidade.** `get_futebol_competitions()` deriva a lista de
  `fact_fixtures.competition`, então "Amistosos" aparece no seletor com jogos e zero oportunidades.
  Aceito: a superfície é de 22 jogos futuros, e o botão de registrar aposta exige uma linha de
  mercado, que não existirá.
- **Sem liquidação, mas com placar ao vivo.** Não há linha em `public.leagues_config`, o coletor do
  Supabase, porque sem odds não há aposta a liquidar. O coletor ao vivo do `data-engineering` é outro
  e recolhe a liga sozinho, o que é desejável: é o que assenta o placar final no mesmo dia.
- **O invariante da expansão fica mais fraco.** A exceção é nominal e explícita, mas é a primeira
  brecha na única guarda automática de registro de liga. O desenho certo a longo prazo é cada liga
  declarar um perfil e as listas serem derivadas dele; isso ficou fora para não refatorar treze
  ligas dentro desta entrega.
- **Alvo de execução: novembro** (janela FIFA de 12 a 17). Esperar permite medir, com quatro rodadas
  de Nations League já jogadas, se a forma zero de fato degradou os scores.

## Implementação (DE#94, 2026-09-22)

Duas decisões que a consequência 1 tinha deixado em aberto ("a spec decide"), fechadas na
implementação da coleta:

**Escalação pré-jogo: o gate FOI estendido para lá.** `fixture_lineups_extractor.py`
(`mode="pregame"`) lê `GCSStorage.get_upcoming_fixture_ids`, que filtra por status/janela de
kickoff sem olhar liga — o "quinto caminho" que os quatro endpoints pós-jogo
(`get_fixture_ids_from_storage`, usado por statistics/events/player_stats/lineups "real") não
cobrem. Decisão: estender o mesmo gate (`config.LEAGUES_INSUMO_IDS`) para
`get_upcoming_fixture_ids` também, em vez de aceitar o custo. Dois motivos: o teto de custo da
decisão 3 é **zero** recorrente, não "baixo" — aceitar ~22 chamadas/T-45min por rodada de
amistosos contradiria a própria decisão; e o gate já existia pronto para os outros quatro
caminhos, então estendê-lo é a mesma linha reaproveitada, não mecanismo novo.

**Onde mora o gate dos quatro endpoints pós-jogo:** centralizado em
`GCSStorage.get_fixture_ids_from_storage` (não em `PerFixtureExtractor` nem em cada
extractor), porque é o único ponto por onde os dois consumidores (`PerFixtureExtractor` — 3
endpoints — e `FixtureLineupsExtractor` em modo current/backfill) leem a lista de fixtures
finalizados. Um gate ali protege os dois de uma vez, sem precisar repetir o filtro.

`LEAGUES_INSUMO_IDS` (`src/config.py`) é a lista nominal que alimenta os dois gates — hoje só
`AMISTOSOS_ID`, mas o nome é genérico (não `AMISTOSOS_*`) porque a próxima competição de
insumo que a decisão 1 já antecipa ("eliminatórias de Copa têm o mesmo perfil") reusa o mesmo
mecanismo sem precisar de código novo.

## Medição (DE#95, 2026-09-23) — o portão fechou contra o passado

**Veredito: o passado não entra.** A decisão 7 desta ADR ("temporada 2026 inteira, 115 jogos")
**não sobrevive à medição** e fica substituída por este registro.

Medido no `analytics-engineering` (PR
[#197](https://github.com/tech-lamjav/analytics-engineering/pull/197), método completo e
reprodução em `docs/TASKF_RESULTADOS.md`, seção "Ticket DE#95"): materializar o cenário "com
amistosos" contra o target de medição `taskF` (nunca `dev`/`prod`) e comparar
`int_futebol_team_form_pit` linha a linha com produção, restrito às âncoras de Copa do Mundo e
Nations League que já têm jogo medido no mart.

| competição | âncoras | sem histórico ANTES | ganhou histórico do zero | Δ médio pp (taxa de vitória) |
|---|---|---|---|---|
| Copa do Mundo | 208 | 48 | 47 | **22,91** |
| Nations League | 312 | 216 | 200 | **24,15** |

O deslocamento médio é **~23 pontos percentuais**, cerca de **90 vezes** a régua de 0,25 pp da
decisão 11 (herdada da task [F] / #92). Não é ruído de recomputação — a #92 mediu esse ruído em
0,00 pp sobre a mesma família de modelo. É o mecanismo que a decisão 2 já previa por escrito,
com números: quase metade das âncoras de Copa do Mundo (47/48) e a maioria das de Nations
League (200/216) tinham `played_total = 0` — estreavam sem forma — e passariam a carregar até
sete rodadas de amistoso como se fossem jogo oficial.

**Consequência para a cadeia:** o **DE#96** ("liga o slug `amistosos` no mart"), que dependia
deste veredito, precisa ser reaberto com escopo revisto — não pode mais assumir que a temporada
inteira entra. O corte temporário em `stg_futebol_fixtures.sql` e a guarda
`assert_amistosos_fora_do_mart` continuam em produção; removê-los segue sendo decisão do #96,
não desta medição.
