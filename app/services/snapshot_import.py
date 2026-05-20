"""Bulk import of `quote_snapshots` from operator-supplied CSV.

This is the Layer A pipe in the data acquisition strategy: ops manually
runs each insurer's public quote calculator for a fixed profile grid,
records the result in a CSV, and uploads it here. Rows land in
`quote_snapshots` with `source='manual'` and `confidence=1.000`. The
rating engine then fits its per-insurer model on the resulting rows so
out-of-grid profiles still get an estimated quote.

CSV columns (header row required)
---------------------------------
- provider_code           e.g. "lalux"
- product_code            e.g. "lalux_car"
- coverage_level          basic | standard | premium
- annual_premium_cents    integer
- deductible_cents        integer
- age_band                e.g. "25-34" (must match a label in provider_config bands)
- vehicle_value_band      e.g. "15001-30000"
- mileage_band            e.g. "8001-15000"
- claims_band             e.g. "0"
- power_band              e.g. "81-110"
- usage_type              private | commute | business
- snapshot_date           YYYY-MM-DD (the date ops collected the quote)
- currency                EUR (optional; defaults to EUR)
- valid_until             YYYY-MM-DD (optional; defaults to snapshot_date + 180 days)
- notes                   free text (optional; goes into offer_details)

Behavior
--------
- Strict CSV: a single invalid row aborts the whole import (atomic).
  Better to fix the CSV than to leave half a batch loaded.
- Idempotent on `(provider_code, product_code, all bands, usage_type,
  coverage_level, snapshot_date)`: an existing row with the same key
  is marked `is_active=False` before the new row is inserted. This
  matches Layer B's behavior so admin imports and scheduled scrapes
  don't fight each other.
- The rating engine cache is invalidated per (provider, product) after
  a successful import — the next request triggers a refit on the
  enlarged training set.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Product, Provider, QuoteSnapshot
from app.services.rating_engine import rating_engine


REQUIRED_COLUMNS = (
    "provider_code",
    "product_code",
    "coverage_level",
    "annual_premium_cents",
    "deductible_cents",
    "age_band",
    "vehicle_value_band",
    "mileage_band",
    "claims_band",
    "power_band",
    "usage_type",
    "snapshot_date",
)

OPTIONAL_COLUMNS = ("currency", "valid_until", "notes")

ALLOWED_COVERAGE_LEVELS = {"basic", "standard", "premium"}
ALLOWED_USAGE_TYPES = {"private", "commute", "business"}

DEFAULT_VALIDITY_DAYS = 180


class CsvImportError(ValueError):
    """Raised when the uploaded CSV is malformed or contains invalid rows."""


@dataclass
class CsvImportResult:
    inserted: int
    deactivated: int
    providers_touched: list[str]
    rows_seen: int


def import_snapshots_csv(session: Session, csv_text: str) -> CsvImportResult:
    """Parse `csv_text`, upsert into `quote_snapshots`, return counts.

    Caller commits the session. We do this so the admin route can wrap
    the import + audit log in a single transaction.
    """

    rows = _parse_csv(csv_text)
    if not rows:
        raise CsvImportError("CSV is empty (no data rows after the header).")

    # Resolve provider/product IDs up front so we fail fast on bad codes.
    provider_lookup = _resolve_providers(session, rows)
    product_lookup = _resolve_products(session, provider_lookup, rows)

    deactivated = 0
    inserted_objects: list[QuoteSnapshot] = []
    touched_pp: set[tuple[str, str]] = set()

    for line_no, row in enumerate(rows, start=2):  # +2: header + 1-indexed
        provider = provider_lookup[row["provider_code"]]
        product = product_lookup[(row["provider_code"], row["product_code"])]
        _validate_row(row, line_no)

        snapshot_date = _parse_date(row["snapshot_date"], "snapshot_date", line_no)
        valid_until = _parse_optional_date(
            row.get("valid_until"), "valid_until", line_no
        ) or (snapshot_date + timedelta(days=DEFAULT_VALIDITY_DAYS))

        # Deactivate any existing row at the same logical key. Note:
        # `source` is intentionally NOT part of the key — an admin
        # re-import supersedes any prior scraper / seed row at that
        # exact band combo, which is what we want.
        deactivated += _deactivate_collisions(
            session, provider.id, product.id, row, snapshot_date
        )

        snapshot = QuoteSnapshot(
            provider_id=provider.id,
            product_id=product.id,
            product_line=product.product_line,
            snapshot_date=snapshot_date,
            valid_until=valid_until,
            coverage_level=row["coverage_level"],
            currency=(row.get("currency") or "EUR").strip().upper(),
            annual_premium_cents=int(row["annual_premium_cents"]),
            deductible_cents=int(row["deductible_cents"]),
            age_band=row["age_band"],
            vehicle_value_band=row["vehicle_value_band"],
            mileage_band=row["mileage_band"],
            claims_band=row["claims_band"],
            power_band=row["power_band"],
            usage_type=row["usage_type"],
            is_active=True,
            source="manual",
            confidence=Decimal("1.000"),
            offer_details={
                "source": "manual",
                "imported_at_line": line_no,
                "notes": (row.get("notes") or "").strip() or None,
            },
        )
        inserted_objects.append(snapshot)
        touched_pp.add((row["provider_code"], row["product_code"]))

    session.add_all(inserted_objects)
    session.flush()

    # Invalidate the rating-engine cache for everything we touched so
    # subsequent /recommend calls see the new data.
    for provider_code, product_code in touched_pp:
        provider = provider_lookup[provider_code]
        product = product_lookup[(provider_code, product_code)]
        rating_engine.invalidate(provider.id, product.id)

    return CsvImportResult(
        inserted=len(inserted_objects),
        deactivated=deactivated,
        providers_touched=sorted({pp[0] for pp in touched_pp}),
        rows_seen=len(rows),
    )


# ---- internals ---------------------------------------------------


def _parse_csv(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise CsvImportError("CSV has no header row.")

    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise CsvImportError(
            f"CSV is missing required columns: {', '.join(missing)}"
        )

    rows: list[dict[str, str]] = []
    for row in reader:
        # Strip whitespace on all values; treat empty strings as missing.
        cleaned = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        rows.append(cleaned)
    return rows


def _resolve_providers(
    session: Session, rows: list[dict[str, str]]
) -> dict[str, Provider]:
    codes = sorted({row["provider_code"] for row in rows})
    providers = list(
        session.execute(select(Provider).where(Provider.code.in_(codes))).scalars()
    )
    by_code = {p.code: p for p in providers}
    missing = [c for c in codes if c not in by_code]
    if missing:
        raise CsvImportError(
            f"Unknown provider_code(s): {', '.join(missing)}. "
            "Seed them or fix the CSV."
        )
    return by_code


def _resolve_products(
    session: Session,
    provider_lookup: dict[str, Provider],
    rows: list[dict[str, str]],
) -> dict[tuple[str, str], Product]:
    keys = sorted({(row["provider_code"], row["product_code"]) for row in rows})
    out: dict[tuple[str, str], Product] = {}
    for provider_code, product_code in keys:
        provider = provider_lookup[provider_code]
        product = session.execute(
            select(Product).where(
                Product.provider_id == provider.id, Product.code == product_code
            )
        ).scalar_one_or_none()
        if product is None:
            raise CsvImportError(
                f"Unknown product_code {product_code!r} for provider {provider_code!r}."
            )
        out[(provider_code, product_code)] = product
    return out


def _validate_row(row: dict[str, str], line_no: int) -> None:
    if row["coverage_level"] not in ALLOWED_COVERAGE_LEVELS:
        raise CsvImportError(
            f"Line {line_no}: coverage_level must be one of "
            f"{sorted(ALLOWED_COVERAGE_LEVELS)}, got {row['coverage_level']!r}."
        )
    if row["usage_type"] not in ALLOWED_USAGE_TYPES:
        raise CsvImportError(
            f"Line {line_no}: usage_type must be one of "
            f"{sorted(ALLOWED_USAGE_TYPES)}, got {row['usage_type']!r}."
        )
    try:
        premium = int(row["annual_premium_cents"])
        deductible = int(row["deductible_cents"])
    except (TypeError, ValueError) as exc:
        raise CsvImportError(
            f"Line {line_no}: annual_premium_cents and deductible_cents must be integers."
        ) from exc
    if premium <= 0:
        raise CsvImportError(f"Line {line_no}: annual_premium_cents must be > 0.")
    if deductible < 0:
        raise CsvImportError(f"Line {line_no}: deductible_cents must be ≥ 0.")


def _parse_date(value: str, field: str, line_no: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CsvImportError(
            f"Line {line_no}: {field} must be YYYY-MM-DD, got {value!r}."
        ) from exc


def _parse_optional_date(
    value: str | None, field: str, line_no: int
) -> date | None:
    if not value:
        return None
    return _parse_date(value, field, line_no)


def _deactivate_collisions(
    session: Session,
    provider_id,
    product_id,
    row: dict[str, str],
    snapshot_date: date,
) -> int:
    """Mark prior active rows at the same logical key as inactive.

    Returns the number of rows deactivated.
    """

    stmt = (
        update(QuoteSnapshot)
        .where(
            QuoteSnapshot.provider_id == provider_id,
            QuoteSnapshot.product_id == product_id,
            QuoteSnapshot.coverage_level == row["coverage_level"],
            QuoteSnapshot.age_band == row["age_band"],
            QuoteSnapshot.vehicle_value_band == row["vehicle_value_band"],
            QuoteSnapshot.mileage_band == row["mileage_band"],
            QuoteSnapshot.claims_band == row["claims_band"],
            QuoteSnapshot.power_band == row["power_band"],
            QuoteSnapshot.usage_type == row["usage_type"],
            QuoteSnapshot.is_active.is_(True),
        )
        .values(is_active=False)
    )
    result = session.execute(stmt)
    return result.rowcount or 0
