"""Base class for per-insurer quote-collection scrapers.

This is the Layer B framework. Each insurer scraper:

1. Subclasses `BaseQuoteScraper` and sets `provider_code`, `product_code`,
   and `ALLOWED_BY_TOS` (initially False — flip to True only after the
   insurer's ToS / robots.txt / rate-limit posture has been reviewed and
   recorded in `docs/scraping_compliance.md`).
2. Implements `collect(profile)` to return a `CollectedQuote` for a
   single (profile × coverage_level) pair. The framework loops over the
   declared profile grid (`profile_grid.yaml`) and aggregates results.
3. Registers itself with the orchestrator by importing this module in
   `app/workers/scrapers/__init__.py` (each module's `_register()`
   adds an entry to `_SCRAPERS`).

What the framework guarantees:
- A scraper with `ALLOWED_BY_TOS = False` is **refused at orchestrator
  level**: `quote_collector.run(...)` raises before any HTTP request is
  attempted. The check is in code, not config, so it can't be bypassed
  by toggling an env var.
- Rate limiting (`MIN_DELAY_SECONDS`) is enforced by the orchestrator
  between requests, not the scraper.
- Failures (HTTP errors, parsing errors) for one profile don't kill the
  whole run — they're collected into a report and the run continues.
- Output snapshots land with `source='scraper'`, `confidence=0.95` (slight
  haircut vs manual to reflect form-parsing risk), `valid_until` =
  snapshot_date + 7 days.

What we deliberately don't put in this module:
- The actual Playwright calls. They live in each per-insurer subclass
  because every insurer's quote form is different. The orchestrator
  doesn't care how the scraper produces a price; it only cares about
  the contract.
- Compliance metadata (ToS link, rate budget). That belongs in
  `docs/scraping_compliance.md` so it's reviewable as a doc, not code.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from playwright.sync_api import Page


# Where `--learn` writes per-insurer captured selectors. Kept next to
# the scraper modules so each insurer's "knowledge" travels with its
# code (and can be reviewed in pull requests).
_SELECTOR_DIR = Path(__file__).resolve().parent / "selectors"


@dataclass(frozen=True)
class CollectionProfile:
    """One row of the profile grid (`profile_grid.yaml`).

    Mirror the bands the engine expects. The orchestrator passes one
    profile at a time to `collect()`; the scraper returns one
    `CollectedQuote` per coverage level it managed to retrieve.
    """

    age_band: str
    vehicle_value_band: str
    mileage_band: str
    claims_band: str
    power_band: str
    usage_type: str
    # Numeric "midpoint" values the scraper feeds into the insurer's
    # form. Bands are used by the engine; the form needs concrete inputs.
    driver_age: int
    vehicle_value_eur: int
    annual_mileage_km: int
    claims_last_5y: int
    vehicle_power_kw: int


@dataclass(frozen=True)
class CollectedQuote:
    """A single price point a scraper extracted from an insurer's calculator."""

    coverage_level: str  # basic | standard | premium
    annual_premium_cents: int
    deductible_cents: int
    currency: str = "EUR"
    snapshot_date: date | None = None  # defaults to today() in the orchestrator
    confidence: Decimal = Decimal("0.950")
    raw: dict | None = None  # whatever the scraper wants to retain for debugging


class ScraperBlockedByToS(RuntimeError):
    """Raised when a scraper is invoked while ALLOWED_BY_TOS=False.

    The orchestrator catches this and surfaces it in the run report;
    individual scrapers should never catch it themselves.
    """


class BaseQuoteScraper(ABC):
    # Subclasses MUST override these.
    provider_code: str = ""
    product_code: str = ""

    # Compliance gate. Flip to True ONLY after recording the per-insurer
    # ToS review outcome in docs/scraping_compliance.md. The orchestrator
    # checks this attribute before every run.
    ALLOWED_BY_TOS: bool = False

    # Public URL hit by the scraper. Documented here so reviewers can
    # cross-check it against the insurer's robots.txt without diffing
    # Playwright code.
    PUBLIC_QUOTE_PAGE: str = ""

    # Minimum delay between requests, enforced by the orchestrator.
    # Insurers' marketing quote pages aren't designed for high throughput;
    # be a good citizen.
    MIN_DELAY_SECONDS: float = 8.0

    def ensure_allowed(self) -> None:
        """Hard gate. Called by the orchestrator before any scrape."""

        if not self.ALLOWED_BY_TOS:
            raise ScraperBlockedByToS(
                f"Scraper for provider {self.provider_code!r} is disabled. "
                "Review ToS, robots.txt, and rate-limit posture, record the "
                "outcome in docs/scraping_compliance.md, then set "
                "ALLOWED_BY_TOS=True in the scraper module."
            )

    @abstractmethod
    def collect(self, profile: CollectionProfile) -> list[CollectedQuote]:
        """Return all coverage levels the scraper could retrieve for `profile`.

        An empty list means "no quote produced for this profile" — that's
        fine, the orchestrator just moves on. Raising means a *bug*; for
        recoverable errors (insurer form returned no price), return [].

        This is the **fully-automated** path. Gated by ALLOWED_BY_TOS.
        Override in subclasses with Playwright code.
        """

    # ---- Tier 2 (human-in-the-loop) hooks --------------------------
    #
    # These deliberately do NOT respect ALLOWED_BY_TOS. The compliance
    # rationale is that every form submission in this path is a real
    # human deciding to submit, in a real browser session — the script
    # only pre-fills and extracts. That's auxiliary tooling for a human
    # user, not automated scraping.
    #
    # Subclasses override these to declare which form fields receive
    # which profile values, and which DOM selectors carry the result
    # prices. Both default to NotImplementedError so the assisted
    # collector reports "not yet supported" for an insurer cleanly.

    def prefill_form(self, page: "Page", profile: CollectionProfile) -> None:
        """Pre-fill the insurer's quote form for `profile`.

        Default implementation reads
        `app/workers/scrapers/selectors/<provider_code>.json` (produced
        by `python -m app.workers.assisted_collector --learn`). Each
        entry under `form_fields` maps a logical name (matching a
        `CollectionProfile` attribute) to either:
          - a CSS selector string, or
          - a `{"selector": "...", "value_map": {...}}` dict for selects
            whose option values don't match the profile field's domain.

        Subclasses can still override this with bespoke Playwright code
        if a particular insurer's form is too dynamic for selector-only
        filling (multi-step wizard, iframes, etc.).
        """

        selectors = self._load_selectors()
        if not selectors or not selectors.get("form_fields"):
            raise NotImplementedError(
                f"prefill_form not implemented for {self.provider_code!r}. "
                "Capture selectors with `python -m app.workers.assisted_collector "
                f"--provider {self.provider_code} --learn`, or override "
                "prefill_form in the scraper module."
            )

        for logical_name, entry in selectors["form_fields"].items():
            selector, value_map = _unpack_selector(entry)
            raw_value = getattr(profile, logical_name, None)
            if raw_value is None:
                # Profile doesn't carry this field (postcode, dob, etc.).
                # The capture run included it; that's fine — the human
                # will fill it manually before submitting.
                continue
            mapped_value = (
                value_map.get(str(raw_value), str(raw_value))
                if value_map
                else str(raw_value)
            )
            _fill_field(page, selector, mapped_value)

    def extract_result(self, page: "Page") -> list[CollectedQuote]:
        """Parse the result page DOM after the human has submitted the quote.

        Default implementation reads `result_elements` from the
        captured selectors JSON. Expects keys named
        `<level>_premium` and (optionally) `<level>_deductible` for
        each of basic / standard / premium.

        Returns one `CollectedQuote` per coverage level whose
        `<level>_premium` selector both exists in the JSON and matches
        a visible element on the page. Missing deductibles default to 0.
        """

        selectors = self._load_selectors()
        if not selectors or not selectors.get("result_elements"):
            raise NotImplementedError(
                f"extract_result not implemented for {self.provider_code!r}. "
                "Capture selectors with `python -m app.workers.assisted_collector "
                f"--provider {self.provider_code} --learn`, or override "
                "extract_result in the scraper module."
            )

        out: list[CollectedQuote] = []
        for level in ("basic", "standard", "premium"):
            premium_entry = selectors["result_elements"].get(f"{level}_premium")
            if not premium_entry:
                continue
            premium_selector, _ = _unpack_selector(premium_entry)
            try:
                premium_text = page.locator(premium_selector).first.inner_text(timeout=2500)
            except Exception:  # noqa: BLE001
                continue
            premium_cents = _euros_text_to_cents(premium_text)
            if premium_cents is None:
                continue

            deductible_cents = 0
            deductible_entry = selectors["result_elements"].get(f"{level}_deductible")
            if deductible_entry:
                deductible_selector, _ = _unpack_selector(deductible_entry)
                try:
                    deductible_text = page.locator(deductible_selector).first.inner_text(
                        timeout=2500
                    )
                    deductible_cents = _euros_text_to_cents(deductible_text) or 0
                except Exception:  # noqa: BLE001
                    pass

            out.append(
                CollectedQuote(
                    coverage_level=level,
                    annual_premium_cents=premium_cents,
                    deductible_cents=deductible_cents,
                )
            )
        return out

    # ---- helpers ----------------------------------------------------

    def _load_selectors(self) -> dict[str, Any] | None:
        path = _SELECTOR_DIR / f"{self.provider_code}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


def _unpack_selector(entry: Any) -> tuple[str, dict[str, str] | None]:
    """Normalize a JSON `form_fields` / `result_elements` value.

    Accepts plain strings (`"input[name='age']"`) or dicts (`{"selector":
    "...", "value_map": {...}}`). Returns `(selector, value_map | None)`.
    """

    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        selector = entry.get("selector", "")
        vm = entry.get("value_map")
        return selector, vm if isinstance(vm, dict) else None
    return "", None


def _fill_field(page: "Page", selector: str, value: str) -> None:
    """Best-effort form fill — handles inputs, selects, and contentEditable."""

    if not selector:
        return
    locator = page.locator(selector).first
    try:
        tag = locator.evaluate("el => el.tagName ? el.tagName.toLowerCase() : ''")
    except Exception:  # noqa: BLE001
        return
    try:
        if tag == "select":
            locator.select_option(value)
        else:
            locator.fill(value)
    except Exception:  # noqa: BLE001
        # If both strategies fail, leave the field empty — the human
        # will see it during the review pause and can fill it manually.
        return


def _euros_text_to_cents(text: str) -> int | None:
    """Parse a price like '€ 730.50 / yr' or '1.250,00 €' into cents.

    Handles the two common decimal/grouping conventions on LU insurer
    sites (en/fr/de). Returns None if no number is found.
    """

    if text is None:
        return None
    # Strip currency symbols and labels.
    cleaned = re.sub(r"[^\d.,\-]", "", text)
    if not cleaned:
        return None

    # Decide which symbol is the decimal separator. If both appear, the
    # rightmost wins (matches both "1,250.00" and "1.250,00").
    last_dot = cleaned.rfind(".")
    last_comma = cleaned.rfind(",")
    if last_dot == -1 and last_comma == -1:
        try:
            return int(cleaned) * 100
        except ValueError:
            return None
    decimal_sep = "." if last_dot > last_comma else ","
    grouping_sep = "," if decimal_sep == "." else "."
    normalised = cleaned.replace(grouping_sep, "").replace(decimal_sep, ".")
    try:
        return int(round(float(normalised) * 100))
    except ValueError:
        return None
