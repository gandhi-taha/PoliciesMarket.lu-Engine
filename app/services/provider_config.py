"""Loader/writer for the DB-driven provider configuration.

Public surface
--------------
- `load_active_config(session, provider_code, product_code)` — returns the
  active `ProviderConfigPayload` for the given codes, or the hardcoded
  defaults if no row exists. Falls back gracefully so the engine works
  out-of-the-box without any provider config row.
- `upsert_config(session, payload)` — creates a new version, deactivates
  prior versions, returns the persisted row.
- `list_configs(session)` — for the admin UI.

The hardcoded defaults mirror the band thresholds that used to live in
`app/services/normalization.py` so behavior is unchanged on day one.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Product, Provider, ProviderConfig
from app.schemas.provider_config import (
    BandConfig,
    BandThreshold,
    ProviderConfigOut,
    ProviderConfigPayload,
    ProviderConfigUpsert,
    TierConfig,
)


def default_payload() -> ProviderConfigPayload:
    """Hardcoded baseline mirroring the pre-DB defaults.

    Kept here (rather than imported from normalization) so the
    normalization service can ask this module for thresholds and we
    don't have a circular dep.
    """

    return ProviderConfigPayload(
        bands=BandConfig(
            age=[
                BandThreshold(max=24, label="18-24"),
                BandThreshold(max=34, label="25-34"),
                BandThreshold(max=49, label="35-49"),
                BandThreshold(max=64, label="50-64"),
                BandThreshold(max=75, label="65-75"),
                BandThreshold(max=None, label="76+"),
            ],
            vehicle_value=[
                BandThreshold(max=15000, label="0-15000"),
                BandThreshold(max=30000, label="15001-30000"),
                BandThreshold(max=50000, label="30001-50000"),
                BandThreshold(max=None, label="50001+"),
            ],
            mileage=[
                BandThreshold(max=8000, label="0-8000"),
                BandThreshold(max=15000, label="8001-15000"),
                BandThreshold(max=25000, label="15001-25000"),
                BandThreshold(max=None, label="25001+"),
            ],
            claims=[
                BandThreshold(max=0, label="0"),
                BandThreshold(max=1, label="1"),
                BandThreshold(max=None, label="2+"),
            ],
            power=[
                BandThreshold(max=80, label="0-80"),
                BandThreshold(max=110, label="81-110"),
                BandThreshold(max=150, label="111-150"),
                BandThreshold(max=None, label="151+"),
            ],
        ),
        tiers=[
            TierConfig(code="basic", name="Basic", coverage_level="basic"),
            TierConfig(code="standard", name="Standard", coverage_level="standard"),
            TierConfig(code="premium", name="Premium", coverage_level="premium"),
        ],
    )


def load_active_config(
    session: Session | None,
    provider_code: str,
    product_code: str,
) -> ProviderConfigPayload:
    """Look up the active config for (provider_code, product_code).

    Falls back to `default_payload()` if:
    - `session` is None (parser pipeline can run without a DB)
    - No matching row exists
    - The row exists but the JSON fails Pydantic validation (logged once,
      defaults returned — refusing to serve traffic over a bad config
      would be worse than serving with defaults)
    """

    if session is None:
        return default_payload()

    row = _fetch_active_row(session, provider_code, product_code)
    if row is None:
        return default_payload()
    try:
        return ProviderConfigPayload.model_validate(row.config_json)
    except Exception:  # noqa: BLE001
        # Bad data in DB — serve defaults rather than 500'ing every request.
        return default_payload()


def upsert_config(
    session: Session, payload: ProviderConfigUpsert
) -> ProviderConfigOut:
    """Insert a new version of the config and deactivate prior versions."""

    provider, product = _resolve_provider_product(
        session, payload.provider_code, payload.product_code
    )

    # Deactivate all existing rows for this pair.
    existing: Sequence[ProviderConfig] = (
        session.execute(
            select(ProviderConfig).where(
                ProviderConfig.provider_id == provider.id,
                ProviderConfig.product_id == product.id,
            )
        )
        .scalars()
        .all()
    )
    next_version = max((row.version for row in existing), default=0) + 1
    for row in existing:
        row.is_active = False

    new_row = ProviderConfig(
        provider_id=provider.id,
        product_id=product.id,
        version=next_version,
        is_active=True,
        config_json=payload.config.model_dump(mode="json"),
        notes=payload.notes,
    )
    session.add(new_row)
    session.flush()

    return _to_out(new_row, provider, product)


def list_configs(session: Session) -> list[ProviderConfigOut]:
    rows = (
        session.execute(
            select(ProviderConfig).options(
                selectinload(ProviderConfig.provider),
                selectinload(ProviderConfig.product),
            )
        )
        .scalars()
        .all()
    )
    return [_to_out(r, r.provider, r.product) for r in rows]


def get_active(
    session: Session, provider_code: str, product_code: str
) -> ProviderConfigOut | None:
    row = _fetch_active_row(session, provider_code, product_code)
    if row is None:
        return None
    provider = row.provider
    product = row.product
    return _to_out(row, provider, product)


# ---- internals -----------------------------------------------------------


def _fetch_active_row(
    session: Session, provider_code: str, product_code: str
) -> ProviderConfig | None:
    return (
        session.execute(
            select(ProviderConfig)
            .join(Provider, ProviderConfig.provider_id == Provider.id)
            .join(Product, ProviderConfig.product_id == Product.id)
            .where(
                Provider.code == provider_code,
                Product.code == product_code,
                ProviderConfig.is_active.is_(True),
            )
            .order_by(ProviderConfig.version.desc())
            .options(
                selectinload(ProviderConfig.provider),
                selectinload(ProviderConfig.product),
            )
            .limit(1)
        )
        .scalars()
        .first()
    )


def _resolve_provider_product(
    session: Session, provider_code: str, product_code: str
) -> tuple[Provider, Product]:
    provider = session.execute(
        select(Provider).where(Provider.code == provider_code)
    ).scalar_one_or_none()
    if provider is None:
        raise LookupError(f"Provider {provider_code!r} not found")
    product = session.execute(
        select(Product).where(
            Product.provider_id == provider.id, Product.code == product_code
        )
    ).scalar_one_or_none()
    if product is None:
        raise LookupError(
            f"Product {product_code!r} not found under provider {provider_code!r}"
        )
    return provider, product


def _to_out(
    row: ProviderConfig, provider: Provider, product: Product
) -> ProviderConfigOut:
    return ProviderConfigOut(
        id=row.id,
        provider_id=row.provider_id,
        product_id=row.product_id,
        provider_code=provider.code,
        product_code=product.code,
        version=row.version,
        is_active=row.is_active,
        config=ProviderConfigPayload.model_validate(row.config_json),
        notes=row.notes,
        updated_at=row.updated_at,
    )
