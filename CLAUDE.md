# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Referências obrigatórias

Sempre leia `.cursorrules` na raiz do projeto antes de escrever código. Ele define os princípios de modularização, a estrutura de `src/`, o padrão de scripts e o que **não** fazer (ex.: nada de `argparse`, nada de hardcode de valores que deveriam vir de `src/config.py`). Essas regras valem igualmente para Claude Code e têm precedência sobre exemplos desatualizados em `README.md`.

## Overview

NBA data extraction pipeline pulling from balldontlie.io into GCS, with BigQuery external tables on top. Each endpoint has its own Cloud Run service; orchestration is done via GCP Workflows (`workflow.yml`, `workflow_injury_report.yml`, `workflow_player_props.yml`). A downstream dbt build is invoked from the workflows.

## Architecture

Data flow: `BallDontLieClient` (src/clients) → `*Extractor` (src/extractors) → `GCSStorage` (src/storage) → GCS bucket `smartbetting-landing` → BigQuery external tables (dataset `nba` in project `smartbetting-dados`, location `us-east1`).

Key layering rules (enforced; see `.cursorrules`):
- Business logic lives in `src/`. Never duplicate it into `scripts/` or `cloud_run/*/main.py`.
- `scripts/extract_*.py` are thin orchestrators that import from `src/` and read everything from `src/config.py`. **Do not add `argparse`** — scripts take no CLI args; configuration is via env vars loaded through `src/config.py`.
- `cloud_run/<service>/main.py` is a `functions_framework` HTTP wrapper that imports and calls `main()` from the matching `scripts/extract_*.py`. Same code runs locally and in Cloud Run.
- At deploy time, `scripts/deploy_cloud_run.sh` copies `src/` and `scripts/` into each `cloud_run/<service>/` directory before `gcloud run deploy`.

`src/extractors/base_extractor.py` defines the `extract()` (abstract) + `extract_and_save()` contract that every extractor follows. GCS paths are built via `get_gcs_path()` in `src/config.py` — reuse it rather than constructing paths ad hoc. Endpoint metadata (has_date, per_page, market) lives in `ENDPOINT_CONFIGS` in the same file.

Note: `README.md` shows extractor scripts being invoked with flags like `--season` / `--dates` — that documentation is stale. Current scripts have no CLI args.

## Common commands

```bash
# Install deps
pip install -r requirements.txt

# GCP auth (ADC)
gcloud auth application-default login

# Run an extractor locally (reads SEASON etc. from .env)
python scripts/extract_games.py
python scripts/extract_player_props.py

# Create / refresh BigQuery external tables
python scripts/create_bigquery_external_tables.py

# Deploy all Cloud Run services
./scripts/deploy_cloud_run.sh

# Deploy a single service
./scripts/deploy_cloud_run.sh extract-games
```

Required env vars in `.env`: `BALLDONTLIE_KEY`, `GCS_BUCKET_NAME`, `GCP_PROJECT_ID`, `SEASON`, `LOG_LEVEL`.

## Endpoints / services

`games`, `game_player_stats`, `season_averages`, `team_season_averages`, `active_players`, `player_injuries`, `team_standings`, `player_props` (DraftKings market). Each has a matching `scripts/extract_*.py`, `cloud_run/extract_*/`, and extractor class in `src/extractors/`.

## Script template

When adding a new script, follow the pattern in `.cursorrules`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractors.<x>_extractor import <X>Extractor
from src.config import SEASON
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

def main():
    try:
        <X>Extractor(season=SEASON).extract_and_save()
        return 0
    except Exception as e:
        logger.error(f"Erro: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

## Agent skills

Some installed skills are user-invocable only — Claude cannot trigger them itself: `/triage`, `/to-tickets`, `/to-spec`, `/implement`, `/wayfinder`, `/grill-with-docs`, `/improve-codebase-architecture`, `/grill-me`, `/handoff`. Whenever the task at hand matches one of these workflows (a feature idea worth ticketing, issues to triage, a plan or spec worth grilling, work worth handing off), proactively recommend the matching command to the user.

### Issue tracker

Issues live in GitHub Issues (tech-lamjav/data-engineering), via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
