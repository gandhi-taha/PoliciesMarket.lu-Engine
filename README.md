# PoliciesMarket.lu Engine

FastAPI backend for PoliciesMarket.lu. It compares car insurance prices from Luxembourg insurers and returns a ranked list of offers for a given driver and car.

## What it is

No Luxembourg insurer offers a public quote API, so we work from quote snapshots: prices collected from each insurer's public calculator for a fixed grid of driver and car profiles. If a request doesn't match a collected profile exactly, a per-insurer pricing model estimates the price and labels it as an estimate with a confidence score. The engine runs behind the platform's Next.js front end. It doesn't sell policies; users go to the insurer to buy.

Four insurers are configured in `providers.yaml`: Foyer, LALUX, Bâloise and AXA Luxembourg. Car insurance only, for now.

## How a recommendation works

`POST /v1/recommend` takes a driver and vehicle profile:

```json
{
  "country": "LU",
  "driver_age": 32,
  "years_licensed": 12,
  "vehicle_value_eur": 22000,
  "vehicle_power_kw": 95,
  "vehicle_age_years": 4,
  "annual_mileage_km": 12000,
  "claims_last_5y": 0,
  "usage_type": "private",
  "coverage_preference": "standard"
}
```

1. We normalise the inquiry into bands (age, vehicle value, mileage, claims, power, usage). Band thresholds come from the `provider_configs` table, with built-in defaults if there's no row.
2. For each enabled insurer, we look up snapshots in the same bands through that insurer's adapter.
3. For any coverage level without an exact match, `app/services/rating_engine.py` fits a log-linear model on that insurer's snapshots: ordinary least squares on log premium, with one fixed effect per band. It needs at least 5 snapshots and won't return a price below 0.60 confidence.
4. We merge offers from all insurers and rank them by the requested coverage level. The request and the result are stored for audit.

Every offer has a `source` (`manual`, `scraper`, `partner_feed`, `estimated` or `seed`) and a `confidence`, so the UI can show you which prices were collected and which are estimates. If you only want one insurer, add `?provider=foyer` (or another code).

Foyer also has a live-feed adapter (`app/providers/foyer_live.py`) with a TTL cache and a fallback to snapshots on HTTP errors. It's only used if a provider entry is switched to `mode="live"`.

## Getting quote data

We get prices in three layers, from most manual to most automated.

- **A. Collected by a person (works today).** Someone runs each insurer's public calculator for the 100 profiles in `app/workers/profile_grid.yaml` and uploads the CSV to `POST /admin/providers/snapshots/import`. The assisted collector (`python -m app.workers.assisted_collector --provider <code>`) opens Chromium via Playwright and pre-fills each form. A person still submits every form and handles any CAPTCHA before it reads the price. Run it once per insurer with `--learn` to record the form selectors. `bootstrap.sh` and `bootstrap.ps1` chain these steps for all enabled insurers and can resume after a break.
- **B. Scheduled collector (built, switched off).** `app/workers/quote_collector.py` is meant to fill the same forms unattended on a weekly Cloud Scheduler job (`deploy/scheduler.yaml`). Every insurer has `allowed_by_tos: false`, and the scraper refuses to run until its section of `docs/scraping_compliance.md` is filled in: ToS, robots.txt, rate budget, contact attempt and legal sign-off. The generic unattended `collect()` isn't implemented yet either.
- **C. Estimates.** The rating engine described above fills the gaps between collected profiles.

Our operator workflow is written up in `docs/ops_collection_runbook.md`.

## Other endpoints

| Endpoint | What it does |
|---|---|
| `POST /v1/query/parse` | Turns a free-text inquiry into structured filters with Mistral. Without `MISTRAL_API_KEY` it only uses the structured fields sent in the request. |
| `POST /v1/search/knowledge` | Keyword plus vector search over policy documents, merged with reciprocal rank fusion. The embeddings are a deterministic hashing placeholder for now, not a trained model. |
| `POST /v1/rag/answer` | Answers a question from those documents with citations (Mistral), or returns an "insufficient evidence" response. |
| `GET /admin/providers/configs`, `GET /admin/providers/configs/{provider}/{product}`, `PUT /admin/providers/configs` | Read and version the band and tier config per insurer. |
| `POST /admin/providers/snapshots/import` | CSV import of collected quotes. Re-uploading replaces earlier rows for the same key. The schema is in `app/services/snapshot_import.py`. |
| `GET /health/live`, `GET /health/ready` | Liveness and readiness probes. |

The `/admin` routes don't have authentication yet, so don't expose them to the public internet.

## What's inside

```
app/
  api/          FastAPI routes
  services/     normalisation, ranking, rating engine, snapshot import, search, RAG
  providers/    adapter registry, snapshot and Foyer live adapters, Mistral client
  models/       SQLAlchemy models (migrations in alembic/)
  workers/      assisted collector, scheduled collector, bootstrap, profile grid
  db/seed.py    creates insurer rows from providers.yaml and loads sample data
docs/           collection runbook and scraping compliance checklist
deploy/         Cloud Scheduler job spec
scripts/        profile grid generator
tests/          pytest suite
```

## How to run

You'll need Python 3.12 and PostgreSQL 16 with the `pgvector` extension. The compose file brings its own Postgres.

With Docker:

```bash
cp .env.example .env    # add MISTRAL_API_KEY if you want the LLM endpoints
docker compose up --build
```

The API container waits for Postgres, runs the migrations and the seed, then serves on port 8000.

Without Docker for the API:

```bash
docker compose up db
pip install -e ".[dev]"
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --reload
python test_req.py      # sends a sample /v1/recommend request
pytest
```

If you want to run the collector, you'll also need `pip install -e ".[collect]"` and `playwright install chromium`.

The seed only loads sample Foyer quotes and documents, tagged `source='seed'`. On startup the API prints a banner telling you which insurers still have no real data.

## Adding an insurer

Add an entry to `providers.yaml`. The adapter registry, the seed and the scraper factory all read it, so you get a snapshot adapter, the database rows and a disabled scraper without touching code. Only write a custom adapter or scraper if the insurer's site needs special handling.

## Status

It's an early draft, version 0.1.0 (May 2026). Only sample data ships with the repo, so our recommendations aren't based on real prices until we've collected quotes. MIT licence.
