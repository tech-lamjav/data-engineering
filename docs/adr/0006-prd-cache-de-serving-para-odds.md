# O Postgres de PRD é cache de serving para as odds: só mercados servidos e retenção de produto

**Status:** proposed (2026-09-29) — vira accepted com o aceite do Victor; aceita, revoga para as odds a cláusula "PRD nunca usa este filtro" da ADR 0003
**Issue:** [DE #109](https://github.com/tech-lamjav/data-engineering/issues/109)

## Contexto

A ADR 0003 decidiu que PRD recebe sempre o histórico completo ("decisão de 08/09 de não tocar
produção") e que só DEV tem retenção. Aquela decisão tratava do **espaço** do DEV. O que a
contradiz é o **tempo** de PRD:

- `fact_odds_snapshot` tem 4,22 mi de linhas (~0,92 GB no Postgres), sai a ~7–9 mil linhas/s e cresce
  na mesma proporção que a coleta. O rebuild das 13 UTC precisa de 1100–1470 s contra o timeout de
  900 s do serviço (números da DE#109, de 28/09). O aumento de timeout da DE#107 compra ~47 dias, não
  mais, e só se o `statement_timeout` de 900 s da sessão do sync também subir: o COPY de odds já teve
  máximo de 862 s nos logs de PRD.
- Só cinco mercados são lidos por alguma RPC do app (`get_futebol_fixture_quotes` é a única que o app
  chama para odds): Match Winner, Both Teams Score, Double Chance, Goals Over/Under e Asian Handicap.
  Dos oito demais (Exact Score é 29,5% da tabela, os cinco de escanteio, 25,9%), só o mercado 45
  (escanteios) tem leitor conhecido, em script de análise; o Asian Handicap, que continua servido,
  também é lido por um script.
- Filtrar em Python, como o DEV faz, não encurta nada: lê tudo do BigQuery e descarta depois.

## Decisão

O Postgres de PRD passa a ser **cache de serving** para `fact_odds_snapshot`:

1. **Só mercados servidos**, no nível do mercado inteiro (não da linha): um filtro por linha
   acoplaria o sync ao corpo da RPC do app.
2. **Retenção de produto por fixture:** fixtures futuras e as dos últimos 30 dias entram com todas as
   janelas; fixtures mais antigas ficam **só com a janela de fechamento (T-15m)**, a linha que
   sustenta o CLV e a "foto" de jogo passado do app. Sem essa exceção, um jogo antigo que teve preço
   passaria a mostrar "sem cotação".
3. **O filtro roda no BigQuery**, por query job com a lista de fixtures elegíveis e um corte de
   partição derivado da coleta mais antiga delas. O skip-if-unchanged continua pelo `modified` do
   BigQuery. A regra (mercados, 30 dias) e a sua versão ficam registradas para que mudar a regra
   force nova carga.
4. **DEV** passa a receber o mesmo filtro de mercados servidos, com a retenção de coleta da DE#106
   também empurrada para o BigQuery.
5. **Carga incremental fica fora de escopo.** Reabre se a leitura filtrada de odds passar de 300 s.

Efeito estimado (29/09/2026), a partir de 598.761 linhas medidas com mercados servidos e 30 dias,
mais a contagem do fechamento antigo: 4,22 mi de linhas caem para ~0,8 mi (~180 MB no Postgres),
com leitura de ~110 s.

## Alternativas consideradas

**Manter a cópia completa do histórico e fazer a carga incremental.** A marca-d'água simples por
momento da captura erra a janela `daily` (20% da tabela), que é recapturada até 7 vezes por dia e
sobrescrita, com mudança de valor, de timestamp e de partição. O desenho correto seria "quente/frio"
(fixtures futuras substituídas por inteiro, passadas só anexadas): bem mais complexo do que o ganho
que a retenção já dá.

**Filtrar por linha das RPCs.** Mais barato (~267 mil linhas contra ~600 mil), mas duplica no sync a
regra hardcoded no corpo de uma RPC do Victor. Quando ele acrescentasse uma linha, o app mostraria
"sem cotação" sem alarme.

**Mart dedicado de serving no dbt.** Mantém a regra "sync copia mart", mas exige selector, workflow e
rebuild de imagem do dbt, e põe uma regra de serving no modelo.

## Consequências

- **Precisa do aceite do Victor antes de implementar** (mudança de contrato de serving). A issue
  aberta PPP#448 dele depende da odd de PRODUÇÃO dos mercados 45 e 56 (escanteios), e três scripts de
  análise do app leem `fact_odds_snapshot` no PRD (escanteios-separação, escanteios-total e
  handicap-premissas). Se ele precisar desses mercados no Postgres, eles entram na lista de mercados
  servidos, só com 30 dias de todas as janelas mais o fechamento antigo; o histórico completo segue
  no BigQuery (snapshot congelado para reprodução).
- **Jogo com mais de 30 dias** perde as janelas que não são de fechamento no PRD (~50% das
  fixtures com odds hoje têm mais de 30 dias), e ~13 jogos passados que nunca tiveram T-15m ficam
  sem cotação.
- **A lista de mercados servidos duplica o que as RPCs leem.** Um teste que lê as RPCs vivas no PRD e
  falha se citarem mercado fora da lista mantém as duas no mesmo passo.
- **A query job exige `bigquery.jobs.create`** na conta de runtime do sync. Preferir uma conta
  dedicada ao sync a ampliar a que os 29 serviços compartilham.
- **A ADR 0003** continua valendo para DEV; a linha de Status dela aponta para esta ADR.
