"""Quote-collector orchestrator (Layer B).

Modelled on the agent-platform CAA scraper (`scripts/caa-sync.ts`):
- one CLI entry point, runs once per invocation
- explicit per-target gate (`ALLOWED_BY_TOS` here, equivalent in spirit
  to the Cloud Scheduler / cron config there)
- writes results into the same table the app reads from
  (`quote_snapshots`), with `source='scraper'`
- fails open: a single bad profile / coverage level doesn't abort the
  whole run

CLI
---
    python -m app.workers.quote_collector --provider foyer
    python -m app.workers.quote_collector --provider foyer --dry-run
    python -m app.workers.quote_collector --provider all

Profile grid lives in `app/workers/profile_grid.yaml`. It's the same
grid ops uses when collecting Layer A quotes manually, so the rating
engine ends up trained on the union of manual + scraped rows with
consistent band assignments.

What this orchestrator deliberately doesn't do
----------------------------------------------
- It doesn't ignore `ALLOWED_BY_TOS`. The check is mandatory and the
  flag is per-module (in code), not config. If a scraper isn't cleared
  it never runs. Period.
- It doesn't retry indefinitely. One attempt per profile, then move on.
  Insurers' forms are flaky; retries multiply load and risk getting us
  rate-limited or banned.
- It doesn't fabricate prices. If no scraper output for a (provider,
  coverage) combination, no row is written — the rating engine handles
  the gap.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Product, Provider, QuoteSnapshot
from app.services.rating_engine import rating_engine
from app.workers.scrapers import get_scraper, list_scrapers
from app.workers.scrapers.base import (
    BaseQuoteScraper,
    CollectedQuote,
    CollectionProfile,
    ScraperBlockedByToS,
)


log = logging.getLogger("quote_collector")

PROFILE_GRID_PATH = Path(__file__).resolve().parent / "profile_grid.yaml"

# Snapshot validity for scraped rows. Short by design: a weekly cron run
# replaces the matrix; if a run fails for two consecutive weeks the
# `valid_until` filter naturally drops stale prices.
SCRAPER_VALIDITY_DAYS = 7


@dataclass
class RunReport:
    provider_code: str
    profiles_attempted: int = 0
    quotes_collected: int = 0
    quotes_persisted: int = 0
    deactivated: int = 0
    blocked: bool = False
    errors: list[str] = field(default_factory=list)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--provider",
        required=True,
        help="Provider code (e.g. 'foyer') or 'all' to attempt every registered scraper.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the scraper but skip DB writes. Still prints the report.",
    )
    args = parser.parse_args(argv)

    profiles = _load_profile_grid()
    if not profiles:
        log.error("Profile grid is empty (%s). Aborting.", PROFILE_GRID_PATH)
        return 2

    targets = _resolve_targets(args.provider)
    if not targets:
        log.error("No scrapers matched %r.", args.provider)
        return 2

    reports: list[RunReport] = []
    for scraper_cls in targets:
        scraper = scraper_cls()
        report = run_for_scraper(scraper, profiles, dry_run=args.dry_run)
        reports.append(report)
        _log_report(report)

    return 0 if all(not r.blocked and not r.errors for r in reports) else 1


def run_for_scraper(
    scraper: BaseQuoteScraper,
    profiles: list[CollectionProfile],
    *,
    dry_run: bool,
) -> RunReport:
    report = RunReport(provider_code=scraper.provider_code)

    try:
        scraper.ensure_allowed()
    except ScraperBlockedByToS as exc:
        report.blocked = True
        report.errors.append(str(exc))
        return report

    collected: list[tuple[CollectionProfile, CollectedQuote]] = []
    for idx, profile in enumerate(profiles):
        report.profiles_attempted += 1
        if idx > 0:
            time.sleep(scraper.MIN_DELAY_SECONDS)  # rate-limit between profiles
        try:
            quotes = scraper.collect(profile)
        except Exception as exc:  # noqa: BLE001 — keep the run going
            report.errors.append(
                f"profile {_profile_key(profile)} raised: {exc!r}"
            )
            continue
        for quote in quotes:
            collected.append((profile, quote))
        report.quotes_collected += len(quotes)

    if dry_run or not collected:
        return report

    persisted, deactivated = _persist_quotes(scraper, collected)
    report.quotes_persisted = persisted
    report.deactivated = deactivated
    return report


def _persist_quotes(
    scraper: BaseQuoteScraper,
    collected: list[tuple[CollectionProfile, CollectedQuote]],
) -> tuple[int, int]:
    session: Session = SessionLocal()
    try:
        provider = (
            session.query(Provider).filter(Provider.code == scraper.provider_code).one_or_none()
        )
        if provider is None:
            log.error(
                "Provider %r not seeded; cannot persist quotes. "
                "Run `python -m app.db.seed` first.",
                scraper.provider_code,
            )
            return 0, 0
        product = (
            session.query(Product)
            .filter(Product.provider_id == provider.id, Product.code == scraper.product_code)
            .one_or_none()
        )
        if product is None:
            log.error(
                "Product %r not seeded for provider %r.",
                scraper.product_code,
                scraper.provider_code,
            )
            return 0, 0

        snapshot_date = date.today()
        valid_until = snapshot_date + timedelta(days=SCRAPER_VALIDITY_DAYS)

        deactivated = 0
        objects = []
        for profile, quote in collected:
            stmt = (
                update(QuoteSnapshot)
                .where(
                    QuoteSnapshot.provider_id == provider.id,
                    QuoteSnapshot.product_id == product.id,
                    QuoteSnapshot.coverage_level == quote.coverage_level,
                    QuoteSnapshot.age_band == profile.age_band,
                    QuoteSnapshot.vehicle_value_band == profile.vehicle_value_band,
                    QuoteSnapshot.mileage_band == profile.mileage_band,
                    QuoteSnapshot.claims_band == profile.claims_band,
                    QuoteSnapshot.power_band == profile.power_band,
                    QuoteSnapshot.usage_type == profile.usage_type,
                    QuoteSnapshot.is_active.is_(True),
                    QuoteSnapshot.source == "scraper",
                )
                .values(is_active=False)
            )
            result = session.execute(stmt)
            deactivated += result.rowcount or 0

            objects.append(
                QuoteSnapshot(
                    provider_id=provider.id,
                    product_id=product.id,
                    product_line=product.product_line,
                    snapshot_date=quote.snapshot_date or snapshot_date,
                    valid_until=valid_until,
                    coverage_level=quote.coverage_level,
                    currency=quote.currency,
                    annual_premium_cents=quote.annual_premium_cents,
                    deductible_cents=quote.deductible_cents,
                    age_band=profile.age_band,
                    vehicle_value_band=profile.vehicle_value_band,
                    mileage_band=profile.mileage_band,
                    claims_band=profile.claims_band,
                    power_band=profile.power_band,
                    usage_type=profile.usage_type,
                    is_active=True,
                    source="scraper",
                    confidence=Decimal(f"{float(quote.confidence):.3f}"),
                    offer_details={
                        "source": "scraper",
                        "scraper_run_at": snapshot_date.isoformat(),
                        "raw": quote.raw,
                    },
                )
            )

        session.add_all(objects)
        session.commit()
        rating_engine.invalidate(provider.id, product.id)
        return len(objects), deactivated
    finally:
        session.close()


def _resolve_targets(target: str) -> list[type[BaseQuoteScraper]]:
    if target == "all":
        return list_scrapers()
    try:
        return [get_scraper(target)]
    except KeyError:
        return []


def _load_profile_grid() -> list[CollectionProfile]:
    """Load `profile_grid.yaml`. YAML is loaded with a tiny custom parser
    so we don't add a runtime dep just for this — the grid is a flat
    list of dicts and that's all we need.

    Falls back to an empty list if the file doesn't exist; the caller
    logs a clear error.
    """

    if not PROFILE_GRID_PATH.exists():
        return []
    text = PROFILE_GRID_PATH.read_text(encoding="utf-8")
    return _parse_simple_yaml_list(text)


def _parse_simple_yaml_list(text: str) -> list[CollectionProfile]:
    """Minimal YAML-ish parser for the constrained grid format.

    Supported: a list of dicts where each dict's values are strings or
    ints. No nested structures, no anchors, no flow style. Matches the
    format documented in `profile_grid.yaml`.
    """

    profiles: list[CollectionProfile] = []
    current: dict[str, str | int] = {}

    def flush() -> None:
        if not current:
            return
        profiles.append(
            CollectionProfile(
                age_band=str(current["age_band"]),
                vehicle_value_band=str(current["vehicle_value_band"]),
                mileage_band=str(current["mileage_band"]),
                claims_band=str(current["claims_band"]),
                power_band=str(current["power_band"]),
                usage_type=str(current["usage_type"]),
                driver_age=int(current["driver_age"]),
                vehicle_value_eur=int(current["vehicle_value_eur"]),
                annual_mileage_km=int(current["annual_mileage_km"]),
                claims_last_5y=int(current["claims_last_5y"]),
                vehicle_power_kw=int(current["vehicle_power_kw"]),
            )
        )
        current.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("- "):
            flush()
            key, _, value = line[2:].partition(":")
            current[key.strip()] = _coerce_scalar(value.strip())
        elif line.startswith("  ") and ":" in line:
            key, _, value = line.strip().partition(":")
            current[key.strip()] = _coerce_scalar(value.strip())
    flush()
    return profiles


def _coerce_scalar(value: str) -> str | int:
    value = value.strip().strip('"').strip("'")
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def _profile_key(profile: CollectionProfile) -> str:
    return "/".join(
        [
            profile.age_band,
            profile.vehicle_value_band,
            profile.mileage_band,
            profile.claims_band,
            profile.power_band,
            profile.usage_type,
        ]
    )


def _log_report(report: RunReport) -> None:
    log.info(
        "[%s] attempted=%d collected=%d persisted=%d deactivated=%d blocked=%s errors=%d",
        report.provider_code,
        report.profiles_attempted,
        report.quotes_collected,
        report.quotes_persisted,
        report.deactivated,
        report.blocked,
        len(report.errors),
    )
    for err in report.errors:
        log.warning("[%s] %s", report.provider_code, err)


if __name__ == "__main__":
    sys.exit(main())
