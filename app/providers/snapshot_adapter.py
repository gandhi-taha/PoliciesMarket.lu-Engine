"""Generic snapshot-mode `ProviderAdapter`.

All non-Foyer insurers (LALUX, Bâloise, AXA, La Luxembourgeoise — and
anything we add later) share identical snapshot-mode behavior:

- look up the Provider + Product rows by `provider_code` + `product_code`
- query `quote_snapshots` filtered by band + usage_type + is_active

Rather than copy-paste a near-identical adapter per insurer, this module
exposes one parameterized class. The per-insurer modules in this package
just bind the class to a `provider_code` and register it.

Foyer keeps its own dedicated `foyer.py` adapter because it also has a
live-mode sibling in `foyer_live.py`; we don't want to perturb that
working code path. New providers should use this generic adapter until
they grow enough custom behavior to justify their own module.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Product, Provider, QuoteSnapshot
from app.providers.base import ProviderAdapter
from app.schemas.recommend import NormalizedInquiry


class SnapshotProviderAdapter(ProviderAdapter):
    """Database-backed adapter usable by any provider seeded in the DB.

    Subclasses set `provider_code`, `product_code`, and `product_line` as
    class attributes. The registry instantiates them with no arguments.
    """

    provider_code: str = ""
    product_code: str = ""
    product_line: str = "car"

    # Subclasses can override to allow snapshot-source filtering. None means
    # "any source". Useful if you want a dev adapter that only returns
    # `source='seed'` rows, for example.
    allowed_sources: tuple[str, ...] | None = None

    def __init__(
        self,
        *,
        provider_code: str | None = None,
        product_code: str | None = None,
        product_line: str | None = None,
    ) -> None:
        if provider_code is not None:
            self.provider_code = provider_code
        if product_code is not None:
            self.product_code = product_code
        if product_line is not None:
            self.product_line = product_line
        if not self.provider_code or not self.product_code:
            raise ValueError(
                "SnapshotProviderAdapter requires both provider_code and product_code"
            )

    def get_provider(self, session: Session) -> Provider:
        provider = session.execute(
            select(Provider).where(Provider.code == self.provider_code)
        ).scalar_one_or_none()
        if provider is None:
            raise ValueError(
                f"Provider {self.provider_code!r} not found. Seed the database first."
            )
        return provider

    def get_product(self, session: Session) -> Product:
        provider = self.get_provider(session)
        product = session.execute(
            select(Product).where(
                Product.provider_id == provider.id, Product.code == self.product_code
            )
        ).scalar_one_or_none()
        if product is None:
            raise ValueError(
                f"Product {self.product_code!r} for provider {self.provider_code!r} "
                "not found. Seed the database first."
            )
        return product

    def find_matching_snapshots(
        self, session: Session, normalized: NormalizedInquiry
    ) -> list[QuoteSnapshot]:
        provider = self.get_provider(session)
        product = self.get_product(session)

        stmt = (
            select(QuoteSnapshot)
            .where(
                QuoteSnapshot.provider_id == provider.id,
                QuoteSnapshot.product_id == product.id,
                QuoteSnapshot.product_line == self.product_line,
                QuoteSnapshot.is_active.is_(True),
                QuoteSnapshot.age_band == normalized.bands.age_band,
                QuoteSnapshot.vehicle_value_band == normalized.bands.vehicle_value_band,
                QuoteSnapshot.mileage_band == normalized.bands.mileage_band,
                QuoteSnapshot.claims_band == normalized.bands.claims_band,
                QuoteSnapshot.power_band == normalized.bands.power_band,
                QuoteSnapshot.usage_type == normalized.usage_type.value,
            )
            .options(
                selectinload(QuoteSnapshot.provider),
                selectinload(QuoteSnapshot.product),
            )
        )
        if self.allowed_sources is not None:
            stmt = stmt.where(QuoteSnapshot.source.in_(self.allowed_sources))
        return list(session.execute(stmt).scalars().all())
