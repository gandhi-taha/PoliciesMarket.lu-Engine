"""Startup data-readiness check.

Runs once on FastAPI app startup. Counts quote_snapshots per provider,
distinguishing seed rows from real (manual / scraper / partner_feed)
data, and writes a clear status banner to stderr. The banner tells
ops:

- Which insurers are configured but have NO real data
- Whether ANY non-seed data exists at all
- The exact command to fix it

This replaces silently serving an empty engine.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.db.session import SessionLocal
from app.models import Provider, QuoteSnapshot
from app.providers.loader import load_providers


log = logging.getLogger("policiesmarket.startup")


def emit_readiness_banner() -> None:
    """Print the data-readiness banner to stderr at boot."""

    entries = load_providers()
    if not entries:
        return  # nothing configured, the no-config case is reported elsewhere

    session = SessionLocal()
    try:
        try:
            rows = session.execute(
                select(
                    Provider.code,
                    QuoteSnapshot.source,
                    func.count(QuoteSnapshot.id),
                )
                .join(QuoteSnapshot, Provider.id == QuoteSnapshot.provider_id)
                .where(QuoteSnapshot.is_active.is_(True))
                .group_by(Provider.code, QuoteSnapshot.source)
            ).all()
        except (OperationalError, ProgrammingError) as exc:
            # Migrations haven't run yet (fresh DB without alembic) —
            # don't print a confusing banner. The entrypoint script
            # runs migrations before uvicorn anyway.
            log.warning("Data readiness check skipped: %s", exc)
            return
    finally:
        session.close()

    counts_by_provider: dict[str, dict[str, int]] = {}
    for code, source, count in rows:
        counts_by_provider.setdefault(code, {})[source] = count

    lines: list[str] = []
    lines.append("")
    lines.append("=" * 70)
    lines.append("  PoliciesMarket Engine — data readiness")
    lines.append("=" * 70)

    has_any_real = False
    missing_real_data: list[str] = []
    for entry in entries:
        if not entry.enabled:
            continue
        by_source = counts_by_provider.get(entry.code, {})
        seed_count = by_source.get("seed", 0)
        real_count = sum(
            v for k, v in by_source.items() if k != "seed"
        )
        if real_count > 0:
            has_any_real = True
            status = f"✓ {real_count} real + {seed_count} seed"
        else:
            missing_real_data.append(entry.code)
            status = f"⚠ {seed_count} seed only (no collected data)"
        lines.append(f"  {entry.code:<12} {entry.name:<40} {status}")

    lines.append("")
    if not has_any_real:
        lines.append("⚠️  NO REAL QUOTE DATA HAS BEEN COLLECTED YET.")
        lines.append("")
        lines.append("   The engine will respond to /v1/recommend but every offer")
        lines.append("   will be flagged source='seed' (placeholder data shipped")
        lines.append("   with the repo, not real LU insurance prices).")
        lines.append("")
        lines.append("   To collect real data, run on your host machine (not in Docker):")
        lines.append("")
        lines.append("       ./bootstrap.sh           # macOS / Linux")
        lines.append("       .\\bootstrap.ps1          # Windows")
        lines.append("")
        lines.append("   The bootstrap script opens a real browser, walks you through")
        lines.append("   each insurer's quote calculator, and uploads results here.")
        lines.append("   First run: ~5-7 hours of clicks (one human at the keyboard).")
        lines.append("   Subsequent quarterly refreshes: ~4-6 hours.")
    elif missing_real_data:
        lines.append(
            f"Partial data: {len(missing_real_data)} of "
            f"{len([e for e in entries if e.enabled])} insurers have no real "
            "collected quotes yet."
        )
        lines.append(
            f"Run `./bootstrap.sh --providers {','.join(missing_real_data)}` "
            "to fill them in."
        )
    else:
        lines.append("✓ All configured insurers have collected quote data.")
    lines.append("=" * 70)
    lines.append("")

    sys.stderr.write("\n".join(lines))
    sys.stderr.flush()
