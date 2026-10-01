"""Harness DE#110: roda o _sync_one_table REAL (src.sync.bq_to_postgres) com uma BQ falsa que gera
linhas no formato de futebol.fact_odds_snapshot, e amostra RSS (VmRSS/VmHWM) durante o COPY."""
import datetime as dt, os, sys, threading, time
sys.path.insert(0, "/repo")  # raiz do repo, montada pelo repro.sh
os.environ.setdefault("BIGQUERY_PROJECT_ID", "x")
import psycopg, psycopg._copy
from unittest.mock import MagicMock
from src.sync import bq_to_postgres as mod

# Configuração por variável de ambiente (regra do repo: scripts sem argparse). O repro.sh a define.
class a:
    rows = int(os.environ.get("ROWS", "1000000"))
    runs = int(os.environ.get("RUNS", "1"))          # N COPYs em sequência, mesma conexão
    dsn = os.environ["PGDSN"]
    poison_at = int(os.environ.get("POISON_AT", "-1"))  # linha inválida: o servidor rejeita o COPY no meio
    prefer_flush = os.environ.get("PREFER_FLUSH") == "1"  # força o flush do psycopg (privado; só p/ comparar)

COLS = ["competition","league_id","season","fixture_id","kickoff_utc","collection_window","collection_timestamp",
 "collection_date","minutes_to_kickoff","bookmaker_id","bookmaker_name","market_id","market_name","outcome_label",
 "outcome_side","line_value","odd_decimal","api_update","extracted_at","dbt_loaded_at"]

def rss():
    d = {}
    for l in open("/proc/self/status"):
        if l.startswith(("VmRSS", "VmHWM")): k, v = l.split(":"); d[k] = int(v.split()[0]) / 1024
    return d
T0 = time.monotonic(); samples = []
def sampler():
    while True:
        r = rss(); samples.append((time.monotonic() - T0, r["VmRSS"])); time.sleep(0.25)
threading.Thread(target=sampler, daemon=True).start()

base = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
def gen(n):
    for i in range(n):
        ts = base + dt.timedelta(seconds=i)
        yield {"competition": "brasileirao", "league_id": "oops" if i == a.poison_at else 71, "season": 2026, "fixture_id": 1000 + i % 5000,
               "kickoff_utc": ts, "collection_window": "t24h", "collection_timestamp": ts,
               "collection_date": ts.date(), "minutes_to_kickoff": 1440, "bookmaker_id": i % 12,
               "bookmaker_name": "Bet365" if i % 2 else "Pinnacle", "market_id": i % 9,
               "market_name": "Match Winner", "outcome_label": "Home", "outcome_side": "home",
               "line_value": None if i % 3 else 2.5, "odd_decimal": 1.5 + (i % 100) / 100,
               "api_update": ts, "extracted_at": ts, "dbt_loaded_at": ts}

def fake_bq(n):
    schema = []
    for c in COLS:
        m = MagicMock(); m.name = c; m.mode = "NULLABLE"; m.field_type = "STRING"; schema.append(m)
    it = MagicMock(); it.schema = schema; it.table.modified = dt.datetime(2026, 9, 29, tzinfo=dt.timezone.utc)
    it.__iter__.side_effect = lambda: gen(n)
    bq = MagicMock(); bq.list_rows.return_value = it
    return bq

print(f"psycopg={psycopg.__version__} libpq={psycopg.pq.version()} rows={a.rows} runs={a.runs}", flush=True)
conn = psycopg.connect(a.dsn, autocommit=False)
with conn.cursor() as cur:
    cur.execute("CREATE TABLE IF NOT EXISTS futebol._sync_state (table_name text primary key, last_synced_bq_modified_time timestamptz, last_synced_at timestamptz)")
conn.commit()
if a.prefer_flush: psycopg._copy.PREFER_FLUSH = True
print("PREFER_FLUSH =", psycopg._copy.PREFER_FLUSH, flush=True)
mod.resolve_regra_retencao = lambda *a_, **k: None
print(f"baseline RSS {rss()['VmRSS']:.0f} MiB", flush=True)
for r in range(a.runs):
    t = time.monotonic()
    try:
      res = mod._sync_one_table(fake_bq(a.rows), conn, "fact_odds_snapshot", "futebol", "futebol",
                              ["fact_odds_snapshot"], force=True, env="prd", sport="futebol")
    except Exception as e:
      print(f"run {r}: EXCECAO apos {time.monotonic()-t:.1f}s: {type(e).__name__}: {str(e).splitlines()[0]}", flush=True); conn.rollback(); continue
    dur = time.monotonic() - t; k = rss()
    print(f"run {r}: {res['rows']} rows in {dur:.1f}s | RSS end {k['VmRSS']:.0f} MiB | HWM {k['VmHWM']:.0f} MiB", flush=True)
step = max(1, len(samples) // 12)
print("timeline(s:MiB) " + " ".join(f"{s:.0f}:{m:.0f}" for s, m in samples[::step]))
