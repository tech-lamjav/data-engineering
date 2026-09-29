# Repro DE#110 — RSS do COPY do sync com servidor lento

`repro.sh` roda o `_sync_one_table` **real** (`src/sync/bq_to_postgres.py`) contra um Postgres em
Docker, com uma BQ falsa que gera linhas no formato de `futebol.fact_odds_snapshot` (18 colunas)
e um proxy TCP (`proxy.py`) que limita a vazão até o servidor. `run_copy.py` amostra o RSS
(`VmRSS`/`VmHWM`) a cada 250 ms.

```bash
scripts/repro_sync_copy_memoria/repro.sh 5000000 1000000      # 1 mi de linhas, servidor a 5 MB/s
scripts/repro_sync_copy_memoria/repro.sh 10000000 1000000 2   # 2 COPYs seguidos na mesma conexão
PSYCOPG=3.3.4 scripts/repro_sync_copy_memoria/repro.sh        # outra versão do psycopg
POISON_AT=300000 scripts/repro_sync_copy_memoria/repro.sh 2000000   # linha inválida no meio: o erro deve subir, sem travar
```

## O que ele mede (29/09/2026, arm64 Docker, Linux, py3.13, psycopg 3.2.3)

| cenário (1 mi linhas)          | sem o fix         | com o fix        |
|--------------------------------|-------------------|------------------|
| servidor rápido                | 73–117 MiB, ~6 s  | 73–75 MiB, ~6 s  |
| servidor a 5 MB/s              | **251 MiB**, 44 s | **74 MiB**, 44 s |
| 2 mi linhas a 5 MB/s           | —                 | 74 MiB, 88 s     |
| 2 COPYs seguidos a 10 MB/s     | 213 → 219 MiB     | 74 → 74 MiB      |

Sem o fix o pico é o backlog (linhas produzidas − linhas que o servidor já consumiu): o COPY
serializa ~220 B/linha (44,1 s × 5 MB/s ≈ 220 MB por 1 mi de linhas) e o servidor drena ~6 s × 5 MB/s
enquanto o produtor ainda gera, daí ~190 B/linha de backlog líquido (+178 MiB medidos). Linear em linhas,
independente de o COPY ser "streaming" no lado Python. O RSS não volta depois do COPY (a libpq não encolhe
o buffer; a conexão é única no `run_sync`). O "≤ ~900 MiB" da tabela inteira (4,2 mi linhas) no comentário
do código é extrapolação linear (220 B × 4,2 mi), teto e não medida.

Custo do flush sem throttle (3 runs, 1 mi linhas): 0 a ~5% de tempo (5,6–5,9 s com fix vs 5,6 s sem, em
outra sessão 5,8–7,7 vs 5,6–6,9: ruído do Docker maior que a diferença). Os "73–117 MiB" sem fix com servidor
rápido são de um run isolado; o normal é 73–75.

## O que ele NÃO prova

Prova o **mecanismo** (crescimento proporcional a quanto o produtor supera o socket) e que o flush
periódico remove essa dependência. Não reproduz byte a byte os 0,876 de `memory/utilizations` de
19/09 nem os 2171 MiB do OOM de 27/09 (o gerador falso é instantâneo; em prod o `list_rows` pagina via
HTTP). Critério de aceite em prod: `memory/utilizations` às 13h sair da faixa 0,53–0,94 para uma linha
baixa e plana.

Também **não cobre**: Supavisor/pooler em session mode (não testado; o fix limita o RSS do cliente de qualquer
forma) e linhas largas (o teto do fix é em LINHAS, `COPY_FLUSH_EVERY_ROWS`, não em bytes) nem a memória por
página do `list_rows` do BQ (ver PR). SSL foi verificado à parte pela revisão adversarial (`sslmode=require`,
400 mil linhas a 5 MB/s: 155 → 75 MiB, sem travar), mas não há modo SSL no `repro.sh`.

`POISON_AT=N` injeta uma linha inválida para conferir que um erro do servidor no meio do COPY sobe como
exceção (sem travar no flush) — igual com e sem o fix. Configuração por variável de ambiente porque a regra do
repo proíbe `argparse` em scripts.
