# Scraping compliance checklist

Required reading before enabling **any** per-insurer scraper. The Layer
B orchestrator (`app/workers/quote_collector.py`) refuses to invoke a
scraper whose `ALLOWED_BY_TOS` flag is `False`, and that flag must
not be flipped until the relevant section below is fully filled in.

This document exists for two reasons:

1. **Non-broker positioning.** Our subscription pays for the
   comparison engine, not for insurance distribution
   ([CAA / IDD context](../README.md#data-acquisition-strategy)).
   Running automated requests against an insurer's quote calculator
   without their knowledge erodes that positioning if the insurer ever
   complains. Per-insurer review keeps the posture defensible.
2. **Operational sanity.** Quote calculators are commercial properties
   with rate-limit and bot-detection logic. A run that gets us
   IP-banned at one of our four target insurers takes weeks to undo
   and breaks both Layer B *and* the manual ops collection that
   shares the same office IPs.

## What "cleared" means

A scraper is cleared (and `ALLOWED_BY_TOS = True` may be set) when:

1. The Terms of Service of the public quote page have been read
   end-to-end and quoted excerpts saved below. Any clause that mentions
   automated access, bots, crawling, or commercial use of the data must
   be cited verbatim.
2. The public `robots.txt` has been retrieved and quoted. If it
   disallows the quote-page path, **stop** — the scraper is not cleared
   regardless of ToS.
3. A rate-limit budget has been chosen and documented (delay between
   requests, max requests per day, max requests per IP).
4. A contact attempt with the insurer's comms / partnerships / IT team
   has been made and documented, with response (or lack of response)
   noted. We are not asking permission — we are creating a paper trail
   that we approached this transparently.
5. Legal sign-off (initials + date) is recorded.

If any of the above is missing, the scraper stays disabled.

## Per-insurer status

### Foyer — `app/workers/scrapers/foyer.py`

- **Status:** ❌ Not cleared. Layer A only (manual CSV import).
- **Public quote page:** _to be confirmed_
- **ToS reviewed:** ☐  (link, date, reviewer)
  - Relevant clauses (verbatim):
- **robots.txt fetched:** ☐  (date, snippet)
- **Rate budget:** ☐  (e.g. 1 req / 8 s, max 200 req/day)
- **Contact attempt:** ☐  (channel, date, outcome)
- **Legal sign-off:** ☐  (initials, date)
- **Notes:**

### LALUX (La Luxembourgeoise) — `app/workers/scrapers/lalux.py`

- **Status:** ❌ Not cleared. Layer A only.
- **Public quote page:** _to be confirmed_
- **ToS reviewed:** ☐
  - Relevant clauses:
- **robots.txt fetched:** ☐
- **Rate budget:** ☐
- **Contact attempt:** ☐
- **Legal sign-off:** ☐
- **Notes:**

### Bâloise Luxembourg — `app/workers/scrapers/baloise.py`

- **Status:** ❌ Not cleared. Layer A only.
- **Public quote page:** _to be confirmed_ (planning doc §3172
  references a public premium calculator)
- **ToS reviewed:** ☐
  - Relevant clauses:
- **robots.txt fetched:** ☐
- **Rate budget:** ☐
- **Contact attempt:** ☐
- **Legal sign-off:** ☐
- **Notes:**

### AXA Luxembourg — `app/workers/scrapers/axa.py`

- **Status:** ❌ Not cleared. Layer A only.
- **Public quote page:** _to be confirmed_
- **ToS reviewed:** ☐
  - Relevant clauses:
- **robots.txt fetched:** ☐
- **Rate budget:** ☐
- **Contact attempt:** ☐
- **Legal sign-off:** ☐
- **Notes:**

## How to enable a scraper

Once an insurer's section above is fully filled in and reviewed:

1. In `app/workers/scrapers/<insurer>.py`, set `ALLOWED_BY_TOS = True`.
2. Implement `collect(profile)` in that same file — one Playwright
   session per profile, return `CollectedQuote` rows for the coverage
   levels the form exposes. Aim to keep this file under 100 lines.
3. Make sure the Playwright dependency is installed in the worker
   image (`pip install playwright && playwright install chromium`).
   The engine container intentionally does **not** ship with Playwright
   today — workers run in a separate image so the API container stays
   slim.
4. Run a dry-run end-to-end:
   ```
   python -m app.workers.quote_collector --provider <insurer> --dry-run
   ```
5. If the dry-run passes, schedule it weekly via the Cloud Scheduler
   config in `deploy/scheduler.yaml`. **Do not** schedule daily — the
   marginal data freshness is not worth the load on the insurer's
   site, and weekly already amortises with the rating engine.

## If an insurer asks us to stop

1. Flip `ALLOWED_BY_TOS = False` in the relevant scraper module and
   delete the active scheduler job (`gcloud scheduler jobs delete
   policiesmarket-collector-<insurer>`).
2. Mark all existing scraper-source snapshots inactive:
   ```sql
   UPDATE quote_snapshots
   SET is_active = FALSE
   WHERE provider_id = (SELECT id FROM providers WHERE code = '<insurer>')
     AND source = 'scraper';
   ```
3. Update this document with the date, contact, and reason. The rating
   engine continues to work on the manually-collected rows that remain.

This response path is fast on purpose — the cost of a wrongful run
outliving the request to stop is much higher than the cost of being
slightly less fresh until we resolve it.
