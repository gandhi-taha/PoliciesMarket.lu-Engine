# policiesmarket-engine

Production-style FastAPI service for Luxembourg insurance recommendations.
Multi-provider out of the box: Foyer, LALUX (La Luxembourgeoise),
Bâloise, AXA Luxembourg. The adapter, configuration, and data-source
layers are pluggable — adding another insurer doesn't require touching
the route code.

This README explains four things:

1. **How to run it** (setup / migrate / seed / serve).
2. **How quotes are returned** (the recommendation flow end to end).
3. **[Data acquisition strategy](#data-acquisition-strategy)** — the
   most important section. It explains how the engine has quote data
   to recommend with, given no Luxembourg insurer ships a live B2C API.
4. **API reference** for the endpoints.

---

## 1. Requirements
- Python 3.12
- PostgreSQL 16 (with `pgvector` extension)

## Setup

```powershell
# from the engine folder, with the workspace venv:
. ..\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --reload
```

`docker-compose up --build` does the same in a container with a
pgvector-enabled Postgres included.

---

## 2. How a `/v1/recommend` call works

```
HTTP POST /v1/recommend
        │
        ▼
┌──────────────────┐
│ recommend route  │  /v1/recommend?provider=<code>  → single insurer
│ app/api/         │  /v1/recommend (no provider)    → multi-insurer
│ recommend.py     │
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│ recommendation_service.py                                        │
│                                                                  │
│  1. Normalize the inquiry into bands                             │
│     (age, vehicle value, mileage, claims, power, usage)          │
│                                                                  │
│  2. For each enabled provider, resolve its ProviderAdapter       │
│     via registry.py and call find_matching_snapshots():          │
│       • snapshot mode → SELECT FROM quote_snapshots              │
│       • live mode     → POST to vendor feed (Foyer only today)   │
│                                                                  │
│  3. For any coverage_level the adapter returned no exact-band    │
│     match for, ask rating_engine.synthesize() to fit + predict   │
│     a price from the insurer's collected snapshots. Outputs are  │
│     tagged source='estimated' with confidence < 1.0.             │
│                                                                  │
│  4. Merge all snapshots, rank globally by coverage_preference,   │
│     persist a QuoteRequest + Recommendation audit row, return.   │
└──────────────────────────────────────────────────────────────────┘
```

Every offer in the response carries a `source` and a `confidence`:

| `source`        | What it means                                              | UI treatment       |
| --------------- | ---------------------------------------------------------- | ------------------ |
| `manual`        | Operator-collected from the insurer's public calculator    | "Verified snapshot from YYYY-MM-DD" |
| `scraper`       | Layer B Playwright collector (ToS-gated, see §3)           | "Verified snapshot from YYYY-MM-DD" |
| `partner_feed`  | Live B2B feed (future state per partnership)               | "Live partner quote"                |
| `estimated`     | Rating engine extrapolation from collected snapshots       | "Estimated · confidence X%"         |
| `seed`          | Bundled sample data shipped with the repo                  | dev-only           |

---

## Data acquisition strategy

The engine works **without insurer APIs** because of how it gets data.
Three layers, in order of effort and freshness. The planning doc
(§2.2, §6, §2987-3000) is the source of truth for the strategy; this
section translates it into code locations.

### Layer A — Operator-collected quote matrix (works today)

What it is:
- Ops runs each insurer's **public quote calculator** for ~30-60
  predefined driver profiles (see `app/workers/profile_grid.yaml` for
  the grid).
- Records the result in a CSV (one row per profile × coverage level).
- Uploads via `POST /admin/providers/snapshots/import`.

Two sub-modes, same compliance posture:

- **Tier 1 (manual)** — fully by hand. ~16-24 hours / quarter for 4
  insurers.
- **Tier 2 (assisted)** — `python -m app.workers.assisted_collector
  --provider <code>` opens Chromium, pre-fills the form from the
  profile grid, waits for the human to submit + handle CAPTCHA, then
  reads the result DOM and uploads the CSV automatically. Same
  compliance story (every request is human-initiated), ~5-10 hours /
  quarter. **Recommended from day one.**

Install Tier 2 prerequisites on the ops machine:

```bash
pip install -e ".[collect]"
playwright install chromium
```

The realistic time + cost numbers and per-quarter schedule live in
[`docs/ops_collection_runbook.md`](docs/ops_collection_runbook.md).

Where it lives:
- Endpoint: [`app/api/admin_providers.py`](app/api/admin_providers.py)
  (the `/snapshots/import` route)
- Logic: [`app/services/snapshot_import.py`](app/services/snapshot_import.py)
- CSV schema: documented in the module docstring of `snapshot_import.py`.

Idempotent: re-uploading a corrected CSV deactivates the previous rows
at the same (provider, product, all bands, coverage, usage_type,
snapshot_date) key. The rating engine cache is invalidated for any
(provider, product) touched, so the next `/v1/recommend` call sees the
new data.

ToS-clean: collecting quotes from a public calculator as a normal user
isn't scraping — it's the calculator's intended use. The output of
that work lands here.

### Layer B — Scheduled Playwright collector (framework built, ToS-gated)

What it is:
- A worker that programmatically fills each insurer's quote form for
  the same profile grid Layer A uses, on a **weekly schedule**.
- Each insurer has its own scraper module under
  [`app/workers/scrapers/`](app/workers/scrapers/) with a hard
  `ALLOWED_BY_TOS = False` gate.

Where it lives:
- Orchestrator: [`app/workers/quote_collector.py`](app/workers/quote_collector.py)
- Base class: [`app/workers/scrapers/base.py`](app/workers/scrapers/base.py)
- Per-insurer stubs: `app/workers/scrapers/{foyer,lalux,baloise,axa}.py`
- Profile grid: [`app/workers/profile_grid.yaml`](app/workers/profile_grid.yaml)
- Compliance checklist (required before enabling):
  [`docs/scraping_compliance.md`](docs/scraping_compliance.md)
- Scheduler config: [`deploy/scheduler.yaml`](deploy/scheduler.yaml)

Why it ships disabled: every scraper raises `ScraperBlockedByToS` until
its per-insurer entry in `docs/scraping_compliance.md` is filled in
(ToS reviewed, robots.txt fetched, rate budget set, contact attempted,
legal sign-off recorded) and the in-code flag is flipped. This is on
purpose — the platform's non-broker positioning (planning doc §247,
§663-665) depends on us not running unauthorised automation against
insurers' commercial properties.

Run manually (after a scraper is cleared):
```
python -m app.workers.quote_collector --provider foyer --dry-run
python -m app.workers.quote_collector --provider foyer
python -m app.workers.quote_collector --provider all
```

Run on a schedule: see [`deploy/scheduler.yaml`](deploy/scheduler.yaml)
for the four Cloud Scheduler entries (one per insurer, staggered
Sunday 04:00–04:45 Europe/Luxembourg, weekly).

### Layer C — Rating-engine extrapolation (always on)

What it is:
- A per-insurer log-linear pricing model
  (`log(price) = intercept + α_age + α_value + α_mileage + α_claims +
  α_power + α_usage`), fit by ordinary least squares from the
  snapshots collected in Layers A and B.
- When a recommendation request lands on a profile the matrix doesn't
  cover exactly, the rating engine synthesises an "estimated" offer.
  Confidence drops with the number of bands the inquiry shares with
  the training set; below confidence 0.60 we refuse to return a number
  rather than fabricate one.

Where it lives:
- [`app/services/rating_engine.py`](app/services/rating_engine.py)

Why this is the centrepiece: without it, a manually-collected matrix
of 50 profiles only serves the 50 exact-band combos. With it, the
same 50 profiles serve the entire band space, with the UI badging
extrapolated answers honestly. This is what the planning doc §531-533
calls the `base_price = f(...) × multipliers + addons` engine.

### How they compose

```
                ┌──────────────────────────────────┐
                │  POST /v1/recommend (inquiry)    │
                └────────────────┬─────────────────┘
                                 │
                                 ▼
        ┌─────────────────────────────────────────────┐
        │ For each enabled adapter:                   │
        │                                             │
        │   exact band match in quote_snapshots ?     │
        │      ├── yes → use it (source = manual /    │
        │      │         scraper / partner_feed /     │
        │      │         seed)                        │
        │      └── no  → ask rating_engine to fit on  │
        │                this provider's snapshots    │
        │                and predict — if confidence  │
        │                ≥ 0.60, return as            │
        │                source='estimated'           │
        └─────────────────────┬───────────────────────┘
                              ▼
                  Merged ranked offers + audit row
```

The result: **every inquiry gets offers from every enabled insurer**,
as long as that insurer has ~5+ collected snapshots in the DB. Layer
A alone is enough to bootstrap; Layer B keeps the matrix fresh; Layer
C makes the matrix's coverage universal.

### What none of these layers do

- **Per-request scraping of insurer sites.** The plan rules this out
  explicitly (§1051, §2975, §3855). We don't ship code for it.
- **Fabricating prices.** No layer returns a number it didn't either
  fit from real data or get from a real snapshot.
- **Distribution.** The engine never sells the policy; the platform
  always redirects out to the insurer for binding. The data we collect
  is for comparison, not for closing contracts.

---

## 3. Dynamic provider layer

Three knobs decouple "what providers exist" from "how they answer":

**1. Adapter registry** ([`app/providers/registry.py`](app/providers/registry.py))
— adapters register against `(provider_code, mode)` pairs.
`get_adapter()` resolves at request time. `iter_enabled_adapters()`
yields one per enabled provider, used by the multi-provider fan-out.

**2. DB-driven configuration** (`provider_configs` table, migration
`0004`) — band thresholds and tier metadata live in JSON, editable at
runtime through admin endpoints:

- `GET  /admin/providers/configs`
- `GET  /admin/providers/configs/{provider}/{product}`
- `PUT  /admin/providers/configs` (creates a new version, deactivates
  the previous one)
- `POST /admin/providers/snapshots/import` (CSV upload — Layer A pipe)

The normalization service reads the active row on every request. If
no row exists, the engine falls back to the same hardcoded thresholds
it shipped with.

**3. Snapshot vs live mode** — set `mode="live"` plus `base_url` /
`api_key` / `timeout_seconds` on a provider entry in
`settings.providers` and `LiveFoyerAdapter` (or a future
`Live<Insurer>Adapter`) will pull quotes from the external feed on
each call, with an in-memory TTL cache and automatic fallback to the
DB snapshot adapter on any HTTP failure.

### Adding a new provider

```python
# app/providers/<name>.py
from app.providers.snapshot_adapter import SnapshotProviderAdapter


class WuestenrotAdapter(SnapshotProviderAdapter):
    provider_code = "wuestenrot"
    product_code = "wuestenrot_car"
    product_line = "car"


def _register() -> None:
    from app.providers.registry import register_adapter
    register_adapter("wuestenrot", mode="snapshot")(WuestenrotAdapter)


_register()
```

Then:

1. Add an entry to `settings.providers` in [`app/config.py`](app/config.py).
2. Import the module in `_autoload_builtin_adapters()` in `registry.py`.
3. Add a `Provider` + `Product` seed entry in `ADDITIONAL_PROVIDERS`
   in [`app/db/seed.py`](app/db/seed.py).
4. Add a scraper stub under `app/workers/scrapers/<name>.py` and a
   section in `docs/scraping_compliance.md`.

The recommend route will resolve the new provider on its next call
and the multi-provider fan-out will include its offers automatically.

---

## 4. API reference

### POST /v1/recommend

Returns ranked offers from one or many providers.

```json
POST /v1/recommend
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

- `POST /v1/recommend` — fan out across all enabled providers, return
  the cheapest match across all of them as `best_match`.
- `POST /v1/recommend?provider=foyer` — single-provider mode (original
  contract). `best_match` is the cheapest within that insurer.

Response shape includes a `source` and `confidence` per snapshot so
the consumer can badge `manual` / `scraper` / `partner_feed` /
`estimated` differently.

### POST /admin/providers/snapshots/import

```
POST /admin/providers/snapshots/import
Content-Type: multipart/form-data
file=@layer_a_lalux_2026Q2.csv
```

CSV schema and idempotency rules are documented in
[`app/services/snapshot_import.py`](app/services/snapshot_import.py).
Response is JSON: `{inserted, deactivated, providers_touched, rows_seen}`.

### Other endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST /v1/query/parse` | Mistral parses free-text inquiries into structured filters |
| `POST /v1/search/knowledge` | Hybrid (keyword + vector) search over policy documents |
| `POST /v1/rag/answer` | Grounded LLM answer with citations |
| `GET /admin/providers/configs` | List provider configs |
| `GET /admin/providers/configs/{provider}/{product}` | Active config |
| `PUT /admin/providers/configs` | Upsert config (new version) |
| `GET /health/live`, `GET /health/ready` | Liveness / readiness probes |

---

## 5. Tests

```
pytest
```

Coverage hits: normalization, ranking, eligibility, query parser,
provider registry, provider config, live-adapter cache/fallback,
knowledge search RRF, RAG grounding, explainer + fallback.
