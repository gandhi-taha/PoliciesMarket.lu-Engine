# Layer A ops collection runbook

The realistic plan for how a person actually puts data into the engine
when no insurer ships a B2C API. Three tiers, each cheaper in
time-per-quarter than the last; pick the one that matches where the
business is today.

## TL;DR — the three tiers

| Tier | What it is | Time / quarter (4 insurers) | Annual cost | ToS exposure |
|---|---|---|---|---|
| **1 — Manual** | Human opens form, fills, submits, copies to CSV. The most defensible position; no automation in the request path. | **16–24 hours** | €1.0k–2.4k | None |
| **2 — Assisted** | `python -m app.workers.assisted_collector` opens a real Chromium browser, pre-fills the form from the profile grid, waits for the human to submit + handle CAPTCHA, then reads the result DOM and writes the CSV row automatically. Every form submission is still a real human action. **Requires a one-time `--learn` capture per insurer (~15 min)** to discover selectors; after that, the per-quarter run is purely auto-fill + review + submit. | **5–10 hours** + **~1 h one-time** | €0.4k–1.0k | None — same as Tier 1 |
| **3 — Unattended Layer B** | Headless Playwright on Cloud Scheduler; no human. The framework is built (`quote_collector.py`) but every per-insurer scraper is gated behind `ALLOWED_BY_TOS=False` until ToS / robots.txt / rate-budget review is done (see `docs/scraping_compliance.md`). | **0 hours** | ~€0 (compute only) | High — per-insurer ToS review required first |

This file used to recommend Tier 1 only. The recommended path now is
**Tier 2 from quarter one** — it's the same compliance posture, cuts
ops time roughly in half, and the per-insurer code you write for it
(form selectors + result-page parsers) is exactly the code Tier 3
will use if and when ToS clears.

## Why Tier 2 isn't risky the way Tier 3 is

The compliance argument for Tier 2 in one sentence: a human is the one
deciding to submit each quote in a real browser session, the script
is auxiliary tooling for that human (the same way an Excel macro is
auxiliary tooling for a data-entry clerk).

What the script does in Tier 2:

- Pre-fills form fields. The human can edit any of them before
  submitting.
- Opens the browser non-headless on the ops machine — the request
  source is the ops user's own IP, with their own browser fingerprint.
- Pauses on every profile and waits for the human to press Enter
  before extracting. If a CAPTCHA appears, the human solves it; the
  script just sits there.
- Extracts the result page's prices via DOM selectors. Reading a
  page you're already authorised to view is not scraping; it's
  copy-paste with extra steps.

What the script does NOT do in Tier 2:

- It does not click "Submit" automatically. The human does.
- It does not iterate without the human pressing Enter.
- It does not bypass CAPTCHA, anti-bot detection, or rate limiting.
- It does not run on a schedule.

If the insurer's ToS forbids automated requests, Tier 2 still complies
because the requests are not automated — they're human-driven. If you
want a different opinion in writing before relying on this, ask
counsel; for our planning posture it lines up with the non-broker
positioning (`docs/scraping_compliance.md`).

## When each tier makes sense

- **Tier 2 from day one.** No reason to type values by hand into 30
  forms when a script can do it. Even if you eventually move to Tier 3
  on some insurers, you'll still use Tier 2 for new insurers (no ToS
  review yet) and for spot-validation of Tier 3's output.
- **Tier 3 per insurer**, only after the compliance checklist is
  filled in for that insurer. Don't enable Tier 3 globally — enable it
  one at a time.
- **Tier 1 stays valid as the fallback** for an insurer whose form is
  too JavaScript-heavy or anti-bot-aggressive to pre-fill reliably.
  Flip on the assisted collector and it'll degrade to manual entry per
  field — the human still sees the profile context in the terminal,
  which is faster than tab-switching to a sheet.

## The grid

`app/workers/profile_grid.yaml` lists the 30 starter profiles. They're
chosen to cover the populous bands of the LU driver population — young
city drivers, mid-30s commuters, 50+ low-mileage owners, retirees, plus
a few claims-history variations.

Each profile names a band combination **and** the midpoint numeric values
to feed into the insurer's form. When you sit down at foyer.lu's
calculator, you copy those numeric values; when you save the row in your
CSV, you copy the band labels.

To grow the grid: add rows to `profile_grid.yaml`. Each new row costs
~20 minutes (5 min × 4 insurers) per round.

## Pre-collection setup (one-time, ~2 hours)

1. **Test driver identity.** Most LU insurer calculators ask for a
   postcode and sometimes a date of birth. Use a real ops postcode
   (e.g. your office) and a fixed DOB per age band. Document the
   mapping in a private sheet so each round uses the same identity per
   profile — the insurer's pricing model is deterministic for fixed
   inputs.
2. **CSV template.** In Google Sheets, create columns matching
   [`app/services/snapshot_import.py`](../app/services/snapshot_import.py):
   ```
   provider_code, product_code, coverage_level,
   annual_premium_cents, deductible_cents,
   age_band, vehicle_value_band, mileage_band, claims_band, power_band,
   usage_type, snapshot_date, currency, valid_until, notes
   ```
   Tip: keep one tab per insurer (so per-insurer collection is
   contiguous), then concatenate to a single CSV for upload.
3. **Pre-fill the band/numeric columns** for all 30 × 3 = 90 rows per
   insurer. Only `annual_premium_cents` and `deductible_cents` change
   round-to-round.

## Per-insurer collection — Tier 2 (recommended)

One-time setup on the ops machine:

```bash
pip install -e ".[collect]"
playwright install chromium
```

### Step 0 (one-time per insurer, ~15 min): capture selectors

The first time you touch a given insurer's quote calculator, run the
labeling session that auto-discovers the form fields and price cells:

```bash
python -m app.workers.assisted_collector --provider lalux --learn
```

Chromium opens with an overlay banner ("◉ PoliciesMarket selector-
capture active"). The terminal walks you through three steps:

1. **Form fields.** Fill in each field on the page once. After each
   one, switch to the terminal and press Enter — it asks "Label?"
   and you type `driver_age`, `vehicle_value_eur`, etc. Type `done`
   when finished with the form.
2. **Submit.** Submit the quote in the browser, solve any CAPTCHA,
   press Enter in the terminal when the result page is loaded.
3. **Prices.** Click each price element on the result page; label
   each as `basic_premium`, `basic_deductible`, `standard_premium`,
   etc. Type `done` to finish.

Output: `app/workers/scrapers/selectors/lalux.json` — committed to the
repo so the entire ops team / future runs reuse it.

After Step 0 the base `BaseQuoteScraper.prefill_form()` and
`.extract_result()` work without per-insurer code. Total time for this
step: ~10-15 minutes per insurer, **once**.

If an insurer's form changes (rare, but happens with site redesigns),
re-run `--learn` to refresh the JSON. No code change needed.

Per insurer per quarter:

```bash
python -m app.workers.assisted_collector --provider lalux
# ...or to dry-run without uploading:
python -m app.workers.assisted_collector --provider lalux --no-upload --out lalux_2026Q2.csv
```

What you'll see — for each of the 30 profiles:

```
[lalux 12/30] profile=35-49 / 30001-50000 / 8001-15000 / 0 / 111-150 / commute
  >> Submit the quote in the browser. Press Enter to extract, 's' to skip, 'q' to quit:
  ✓ basic: €580/yr, deductible €650
  ✓ standard: €730/yr, deductible €420
  ✓ premium: €910/yr, deductible €300
```

At the end the script writes `lalux_<YYYYMMDD>.csv` and (unless
`--no-upload`) POSTs it to `http://localhost:8000/admin/providers/snapshots/import`.
Returns:

```json
{"inserted": 90, "deactivated": 90, "providers_touched": ["lalux"], "rows_seen": 90}
```

Old snapshots at the same (provider, product, bands, coverage, usage,
snapshot_date) key are marked inactive automatically — you can safely
re-upload a corrected CSV. The rating engine cache is invalidated, so
the next `/v1/recommend` reflects the new data.

### When you need to override prefill_form / extract_result in code

Most insurers won't need this — `--learn` produces JSON that the
default `BaseQuoteScraper` methods read at runtime. Override in
`app/workers/scrapers/<insurer>.py` only when:

- The form is a multi-step wizard (each step has its own URL).
- A field is inside an iframe.
- The result page renders via JS and selectors need `page.wait_for_*`.
- An option value in a `<select>` doesn't match the profile field's
  domain — in which case the simpler fix is to edit the captured JSON
  by hand to add a `value_map`:

  ```json
  {
    "form_fields": {
      "usage_type": {
        "selector": "select[name='usage']",
        "value_map": {
          "private": "PRIVE",
          "commute": "DOMICILE_TRAVAIL",
          "business": "PROFESSIONNEL"
        }
      }
    }
  }
  ```

## Per-insurer collection — Tier 1 (fallback / first round)

If you haven't filled in the selectors for an insurer yet, the
assisted collector still works — it just prompts you to type each
quote into the terminal:

```
  !! prefill_form not implemented for 'baloise'. Falling back to manual entry.
  >> Submit the quote in the browser. Press Enter to extract, 's' to skip, 'q' to quit:
    basic annual premium (€, blank to skip): 580
    basic deductible (€, blank=0): 650
    standard annual premium (€, blank to skip): 730
    ...
```

Cleaner than the old CSV-in-a-sheet workflow because you don't have to
manage 11 columns by hand — the script knows the profile.

## Per-quarter calendar

| Week | Task |
|---|---|
| Q-start week 1 | Foyer — 4-6 hours collection + upload |
| Q-start week 1 | LALUX — 4-6 hours |
| Q-start week 2 | Bâloise — 4-6 hours |
| Q-start week 2 | AXA — 4-6 hours |
| Q-start week 2 | QA: pick 10 random profiles, run `/v1/recommend`, sanity-check the returned prices vs the source-of-truth sheet |

Two weeks of part-time work per quarter. One person can do this
alongside other work; an intern or freelancer can do it as a focused
gig.

## Common failures

- **Form refuses to quote** (no Lux license, postal restriction, etc.)
  → record a `NULL` in the sheet, skip that row. The rating engine
  fills the gap from the rest.
- **Price comes back as a range** rather than a number → use the midpoint.
- **Insurer requires a phone number to see the price** → that profile
  is unviable; remove it from the grid.
- **Bot challenge / CAPTCHA** → you're a human, solve it. If it fires
  on every profile, slow down (longer pause between submissions).
  If it persists, that insurer's quote page is unsuitable for Layer A
  and you'll need to escalate to either a partnership conversation or
  drop that insurer from the comparison.

## When does Layer A stop being enough?

When **any one** of these is true:

- You're at 10+ insurers (60+ hours/quarter is too much for one person).
- You want weekly freshness rather than quarterly (4× the work).
- Multiple product lines (car + home + life) compound the matrix.

Then it's time to flip ALLOWED_BY_TOS for the cleared insurers and let
Layer B handle the recurring collection. Layer A continues to be useful
for new insurers and for spot-validation of Layer B output.
