#!/bin/bash
# Repro da DE#110: RSS do COPY real (_sync_one_table) com servidor Postgres lento.
# Uso: [POISON_AT=N] [PREFER_FLUSH=1] [PSYCOPG=3.3.4] scripts/repro_sync_copy_memoria/repro.sh [BYTES_POR_S] [LINHAS] [RUNS]
#   BYTES_POR_S  vazão máxima do "servidor" (proxy TCP); 0 = sem limite. Default 5000000.
#   LINHAS       linhas geradas no formato de futebol.fact_odds_snapshot. Default 1000000.
#   RUNS         COPYs em sequência na MESMA conexão (como o run_sync). Default 1.
# Requer Docker. Saída: RSS ao longo do COPY. Verde = HWM ~ baseline (~75 MiB), independente da vazão.
set -euo pipefail
RATE=${1:-5000000}; ROWS=${2:-1000000}; RUNS=${3:-1}; PSYCOPG=${PSYCOPG:-3.2.3}
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
IMG=de110-repro:$PSYCOPG
docker build -q --build-arg PSYCOPG="$PSYCOPG" -t "$IMG" "$HERE" >/dev/null
docker network create de110-repro >/dev/null 2>&1 || true
if ! docker ps --format '{{.Names}}' | grep -qx de110-pg; then
  docker rm -f de110-pg >/dev/null 2>&1 || true
  docker run -d --name de110-pg --network de110-repro -e POSTGRES_PASSWORD=pw postgres:17 \
    -c fsync=off -c synchronous_commit=off >/dev/null
  # -h 127.0.0.1 (TCP): o socket unix já responde no servidor temporário do initdb.
  for _ in $(seq 1 60); do
    docker exec de110-pg pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
  docker exec de110-pg pg_isready -h 127.0.0.1 -U postgres >/dev/null || { echo "postgres não subiu"; exit 1; }
fi
docker exec -i de110-pg psql -U postgres -q -v ON_ERROR_STOP=1 < "$HERE/ddl.sql" >/dev/null 2>&1 \
  || { echo "falha ao aplicar ddl.sql"; exit 1; }
docker run --rm --network de110-repro -m 2g -v "$REPO/src":/repo/src:ro -v "$HERE":/h:ro \
  -e PGDSN="postgresql://postgres:pw@127.0.0.1:6432/postgres" -e ROWS="$ROWS" -e RUNS="$RUNS" \
  -e POISON_AT="${POISON_AT:--1}" -e PREFER_FLUSH="${PREFER_FLUSH:-0}" "$IMG" bash -c \
  "python /h/proxy.py 6432 de110-pg 5432 $RATE & sleep 1; python /h/run_copy.py"
# limpeza: docker rm -f de110-pg && docker network rm de110-repro
