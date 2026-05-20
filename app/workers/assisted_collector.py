"""Human-in-the-loop assisted collector (Tier 2 automation).

Cuts a Layer A round from ~5h to ~1-2h per insurer by:

1. Opening a real Chromium browser on the ops machine (non-headless).
2. Pre-filling the insurer's quote form with the profile-grid values
   the human would otherwise type by hand.
3. Pausing for the human to: glance at the form, submit, solve any
   CAPTCHA, and confirm the result page rendered.
4. Reading the result page DOM and converting it into rows of the
   same CSV format `POST /admin/providers/snapshots/import` expects.

Why this isn't ToS-risky in the way full Layer B is
----------------------------------------------------
- Every form submission is initiated by a real human pressing the
  insurer's "Get my quote" button in their own browser.
- The script's role is purely auxiliary: typing the form values and
  parsing the result page. A human doing the same data entry in
  Excel via macros isn't doing anything different.
- We deliberately do not use ALLOWED_BY_TOS here — that flag is for
  the unattended scheduled scraper. Assisted mode is closer to "Excel
  macro" than "scraping bot".

Usage
-----
    pip install -e ".[collect]"
    playwright install chromium

    python -m app.workers.assisted_collector --provider lalux

    # ...or run all four in sequence (rotates through them):
    python -m app.workers.assisted_collector --provider all

    # ...or just write the CSV to disk without uploading it:
    python -m app.workers.assisted_collector --provider lalux \
        --out lalux_2026Q2.csv --no-upload

What the human sees
-------------------
A terminal that says:

    [lalux 12/30] profile=35-49 / 30001-50000 / 8001-15000 / 0 / 111-150 / commute
    >> Browser open at https://www.lalux.lu/ ...
    >> Form pre-filled. Submit the quote, then press Enter (or 's' to skip).

When the human presses Enter the script extracts the prices, prints
them for visual confirmation, and moves to the next profile.

The full automated Tier 3 path (`quote_collector.py`) and this Tier 2
path share `BaseQuoteScraper.prefill_form()` + `.extract_result()` —
once an insurer's selectors work in Tier 2, flipping ALLOWED_BY_TOS
makes them work in Tier 3 too. There's no double-implementation.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from app.workers.quote_collector import (
    PROFILE_GRID_PATH,
    _load_profile_grid,
    _profile_key,
)
from app.workers.scrapers import get_scraper, list_scrapers
from app.workers.scrapers.base import (
    BaseQuoteScraper,
    CollectedQuote,
    CollectionProfile,
)

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import Browser, Page

log = logging.getLogger("assisted_collector")


CSV_VALIDITY_DAYS = 180


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--provider",
        required=True,
        help="Provider code ('foyer' / 'lalux' / 'baloise' / 'axa') or 'all'.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write CSV to this path instead of uploading. Default name "
        "<provider>_<YYYYMMDD>.csv if omitted.",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip POSTing the CSV to the engine; write to disk only.",
    )
    parser.add_argument(
        "--engine-url",
        default="http://localhost:8000",
        help="Engine base URL for the upload step. Default: http://localhost:8000",
    )
    parser.add_argument(
        "--start-at",
        type=int,
        default=0,
        help="0-indexed profile to start at (resume after a break).",
    )
    parser.add_argument(
        "--learn",
        action="store_true",
        help="One-time per insurer: run an interactive labeling session that "
        "captures form-field + result-page selectors into "
        "app/workers/scrapers/selectors/<provider>.json. Subsequent runs use "
        "those selectors for full Tier 2 speedup.",
    )
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        log.error(
            "Playwright is not installed. Run `pip install -e .[collect]` and "
            "`playwright install chromium`."
        )
        return 2

    profiles = _load_profile_grid()
    if not profiles:
        log.error("Profile grid is empty (%s).", PROFILE_GRID_PATH)
        return 2

    if args.start_at >= len(profiles):
        log.error("--start-at %d is past the end of the grid (%d profiles).",
                  args.start_at, len(profiles))
        return 2

    targets = _resolve_targets(args.provider)
    if not targets:
        log.error("No scrapers matched %r.", args.provider)
        return 2

    overall_rc = 0
    for scraper_cls in targets:
        scraper = scraper_cls()
        if args.learn:
            rc = run_learn_mode(scraper)
        else:
            rc = run_for_scraper(
                scraper,
                profiles[args.start_at :],
                out_path=args.out,
                upload=not args.no_upload,
                engine_url=args.engine_url,
                start_offset=args.start_at,
            )
        overall_rc = overall_rc or rc
    return overall_rc


def run_learn_mode(scraper: BaseQuoteScraper) -> int:
    """Drive the selector-capture flow for one insurer."""

    # Local import so `--learn` doesn't pull selector_capture (which
    # imports playwright types) at module load time.
    from app.workers.scrapers.selector_capture import run_capture

    print()
    print(f"=== {scraper.provider_code.upper()} — selector capture ===")
    print(f"Opens {scraper.PUBLIC_QUOTE_PAGE} in Chromium.")
    print()
    print("Plan:")
    print("  1. Fill the form normally. After each field, hit Enter here to label it.")
    print("  2. Submit the quote.")
    print("  3. Click each price/deductible on the result page; label each.")
    print()

    with _playwright_session(scraper.PUBLIC_QUOTE_PAGE) as page:
        try:
            run_capture(page, scraper.provider_code)
        except KeyboardInterrupt:
            print()
            print("Interrupted — partial capture not saved.")
            return 1
    print()
    print(
        f"Done. Next run `python -m app.workers.assisted_collector "
        f"--provider {scraper.provider_code}` to use the captured selectors."
    )
    return 0


def run_for_scraper(
    scraper: BaseQuoteScraper,
    profiles: list[CollectionProfile],
    *,
    out_path: Path | None,
    upload: bool,
    engine_url: str,
    start_offset: int,
) -> int:
    rows: list[dict[str, str]] = []
    skipped = 0
    today = date.today()
    valid_until = today + timedelta(days=CSV_VALIDITY_DAYS)

    print()
    print(f"=== {scraper.provider_code.upper()} — {len(profiles)} profile(s) ===")
    print(f"Page: {scraper.PUBLIC_QUOTE_PAGE}")
    print()

    with _playwright_session(scraper.PUBLIC_QUOTE_PAGE) as page:
        for idx, profile in enumerate(profiles):
            position = start_offset + idx + 1
            total = start_offset + len(profiles)
            header = f"[{scraper.provider_code} {position}/{total}] profile={_profile_key(profile)}"
            print()
            print(header)

            try:
                scraper.prefill_form(page, profile)
            except NotImplementedError as exc:
                print(f"  !! {exc}")
                print(
                    "  !! Falling back to manual entry: complete the form by hand, "
                    "then press Enter to extract."
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  !! Prefill failed: {exc!r}")
                print("  !! Complete the form by hand, then press Enter.")

            action = _prompt_human(
                "  >> Submit the quote in the browser. Press Enter to extract, 's' to skip, 'q' to quit: "
            )
            if action == "q":
                print("  -- Quitting.")
                break
            if action == "s":
                print("  -- Skipped.")
                skipped += 1
                continue

            try:
                quotes = scraper.extract_result(page)
            except NotImplementedError as exc:
                print(f"  !! {exc}")
                print("  !! Enter prices manually for this profile:")
                quotes = _manual_quote_entry()
            except Exception as exc:  # noqa: BLE001
                print(f"  !! Extraction failed: {exc!r}")
                print("  !! Enter prices manually for this profile:")
                quotes = _manual_quote_entry()

            if not quotes:
                print("  -- No quotes captured for this profile.")
                continue

            for q in quotes:
                row = {
                    "provider_code": scraper.provider_code,
                    "product_code": scraper.product_code,
                    "coverage_level": q.coverage_level,
                    "annual_premium_cents": str(q.annual_premium_cents),
                    "deductible_cents": str(q.deductible_cents),
                    "age_band": profile.age_band,
                    "vehicle_value_band": profile.vehicle_value_band,
                    "mileage_band": profile.mileage_band,
                    "claims_band": profile.claims_band,
                    "power_band": profile.power_band,
                    "usage_type": profile.usage_type,
                    "snapshot_date": today.isoformat(),
                    "currency": q.currency,
                    "valid_until": valid_until.isoformat(),
                    "notes": f"assisted-collector run {today.isoformat()}",
                }
                rows.append(row)
                print(
                    f"  ✓ {q.coverage_level}: €{q.annual_premium_cents/100:.0f}/yr, "
                    f"deductible €{q.deductible_cents/100:.0f}"
                )

            _navigate_back_for_next_profile(page, scraper)

    if not rows:
        print()
        print(f"No rows captured for {scraper.provider_code}. (skipped={skipped})")
        return 0

    csv_text = _rows_to_csv(rows)
    target_path = out_path or Path(
        f"{scraper.provider_code}_{today.strftime('%Y%m%d')}.csv"
    )
    target_path.write_text(csv_text, encoding="utf-8")
    print()
    print(f"Wrote {len(rows)} row(s) → {target_path}")

    if upload:
        rc = _upload_to_engine(target_path, engine_url)
        if rc != 0:
            return rc
    return 0


@contextmanager
def _playwright_session(landing_url: str):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # Non-headless on purpose — the human is driving.
        browser: Browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page: Page = context.new_page()
        page.goto(landing_url)
        try:
            yield page
        finally:
            context.close()
            browser.close()


def _navigate_back_for_next_profile(page, scraper: BaseQuoteScraper) -> None:
    """Reset between profiles. Default: re-navigate to the landing page.

    Per-insurer scrapers can override this if their form keeps state
    across submissions or they need to click a 'new quote' button.
    """

    try:
        page.goto(scraper.PUBLIC_QUOTE_PAGE)
    except Exception:  # noqa: BLE001 — best-effort
        log.warning("Could not navigate back to %s", scraper.PUBLIC_QUOTE_PAGE)


def _prompt_human(prompt: str) -> str:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return "q"
    return answer


def _manual_quote_entry() -> list[CollectedQuote]:
    """Fallback when DOM extraction isn't ready for an insurer yet.

    Asks the human to type prices into the terminal — still faster
    than copy-pasting to a sheet because we skip the band columns
    (already known from the profile).
    """

    quotes: list[CollectedQuote] = []
    for level in ("basic", "standard", "premium"):
        raw = input(
            f"    {level} annual premium (€, blank to skip): "
        ).strip()
        if not raw:
            continue
        try:
            premium_eur = float(raw.replace(",", "."))
        except ValueError:
            print("    !! Not a number, skipping.")
            continue
        ded_raw = input(f"    {level} deductible (€, blank=0): ").strip()
        deductible_eur = 0.0
        if ded_raw:
            try:
                deductible_eur = float(ded_raw.replace(",", "."))
            except ValueError:
                print("    !! Not a number, defaulting to 0.")
        quotes.append(
            CollectedQuote(
                coverage_level=level,
                annual_premium_cents=int(round(premium_eur * 100)),
                deductible_cents=int(round(deductible_eur * 100)),
            )
        )
    return quotes


def _rows_to_csv(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _upload_to_engine(csv_path: Path, engine_url: str) -> int:
    try:
        import httpx
    except ImportError:
        log.error("httpx not available; cannot upload. Save the CSV and upload manually.")
        return 2

    url = engine_url.rstrip("/") + "/admin/providers/snapshots/import"
    print(f"Uploading {csv_path.name} → {url} ...")
    try:
        with csv_path.open("rb") as fh:
            response = httpx.post(
                url,
                files={"file": (csv_path.name, fh, "text/csv")},
                timeout=30.0,
            )
    except httpx.HTTPError as exc:
        print(f"!! Upload failed: {exc!r}")
        return 2

    if response.status_code >= 300:
        print(f"!! Engine returned {response.status_code}: {response.text[:500]}")
        return 1
    print(f"   {response.json()}")
    return 0


def _resolve_targets(target: str) -> list[type[BaseQuoteScraper]]:
    if target == "all":
        return list_scrapers()
    try:
        return [get_scraper(target)]
    except KeyError:
        return []


if __name__ == "__main__":
    sys.exit(main())
