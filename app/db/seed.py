"""DB seed.

Two responsibilities:

1. **Roster.** For every entry in `providers.yaml`, ensure a Provider,
   Product, and (default) ProviderConfig row exists. Idempotent — safe
   to re-run on every boot (the Docker entrypoint does just that).

2. **Sample data.** A small Foyer-only set of sample quote snapshots
   + knowledge documents so a fresh DB has something to recommend
   against and search through before ops collects real data. Tagged
   `source='seed'` so the startup banner and the rating engine treat
   it as placeholder, not real prices.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import (
    Document,
    DocumentChunk,
    Product,
    Provider,
    ProviderConfig,
    QuoteSnapshot,
)
from app.providers.loader import ProviderEntry, load_providers
from app.services.embedding import DeterministicEmbeddingProvider
from app.services.provider_config import default_payload


_embedding_provider = DeterministicEmbeddingProvider()


# Foyer-only sample quotes shipped with the repo so a fresh DB isn't
# empty. Real LU prices land in quote_snapshots with source != 'seed'
# once ops runs the assisted collector.
_SAMPLE_SNAPSHOT_DATE = date(2026, 5, 1)
_SAMPLE_SNAPSHOTS: list[dict] = [
    # 25-34, mid-value car, mid-mileage, no claims, mid power, private
    {"coverage_level": "basic",    "annual_premium_cents": 55000, "deductible_cents": 60000, "age_band": "25-34", "vehicle_value_band": "15001-30000", "mileage_band": "8001-15000", "claims_band": "0", "power_band": "81-110", "usage_type": "private"},
    {"coverage_level": "standard", "annual_premium_cents": 70000, "deductible_cents": 40000, "age_band": "25-34", "vehicle_value_band": "15001-30000", "mileage_band": "8001-15000", "claims_band": "0", "power_band": "81-110", "usage_type": "private"},
    {"coverage_level": "premium",  "annual_premium_cents": 90000, "deductible_cents": 25000, "age_band": "25-34", "vehicle_value_band": "15001-30000", "mileage_band": "8001-15000", "claims_band": "0", "power_band": "81-110", "usage_type": "private"},
    # Same driver, higher mileage + 1 claim
    {"coverage_level": "standard", "annual_premium_cents": 82000, "deductible_cents": 45000, "age_band": "25-34", "vehicle_value_band": "15001-30000", "mileage_band": "15001-25000", "claims_band": "1", "power_band": "81-110", "usage_type": "private"},
    {"coverage_level": "premium",  "annual_premium_cents": 104000, "deductible_cents": 30000, "age_band": "25-34", "vehicle_value_band": "15001-30000", "mileage_band": "15001-25000", "claims_band": "1", "power_band": "81-110", "usage_type": "private"},
    # 35-49, higher-value car, commute
    {"coverage_level": "basic",    "annual_premium_cents": 62000, "deductible_cents": 65000, "age_band": "35-49", "vehicle_value_band": "30001-50000", "mileage_band": "8001-15000", "claims_band": "0", "power_band": "111-150", "usage_type": "commute"},
    {"coverage_level": "standard", "annual_premium_cents": 81000, "deductible_cents": 42000, "age_band": "35-49", "vehicle_value_band": "30001-50000", "mileage_band": "8001-15000", "claims_band": "0", "power_band": "111-150", "usage_type": "commute"},
    {"coverage_level": "premium",  "annual_premium_cents": 99000, "deductible_cents": 30000, "age_band": "35-49", "vehicle_value_band": "30001-50000", "mileage_band": "8001-15000", "claims_band": "0", "power_band": "111-150", "usage_type": "commute"},
]


def seed_data(session: Session) -> None:
    """Idempotent seed. Run on every container boot."""

    # 1. Roster from providers.yaml
    roster: dict[str, tuple[Provider, Product]] = {}
    for entry in load_providers():
        roster[entry.code] = _ensure_provider_product(session, entry)

    # 2. Foyer-only sample quotes (only if there are no Foyer rows yet)
    foyer = roster.get("foyer")
    if foyer is not None:
        _ensure_sample_quotes(session, *foyer)
        _ensure_sample_documents(session, *foyer)


def _ensure_provider_product(
    session: Session, entry: ProviderEntry
) -> tuple[Provider, Product]:
    provider = (
        session.query(Provider).filter(Provider.code == entry.code).one_or_none()
    )
    if provider is None:
        provider = Provider(code=entry.code, name=entry.name, is_active=True)
        session.add(provider)
        session.flush()

    product = (
        session.query(Product)
        .filter(Product.provider_id == provider.id, Product.code == entry.product_code)
        .one_or_none()
    )
    if product is None:
        product = Product(
            provider_id=provider.id,
            code=entry.product_code,
            name=entry.product_name,
            product_line=entry.product_line,
            is_active=True,
        )
        session.add(product)
        session.flush()

    _ensure_provider_config(session, provider, product)
    return provider, product


def _ensure_provider_config(
    session: Session, provider: Provider, product: Product
) -> None:
    """Baseline DB-driven config row. Idempotent."""

    existing = (
        session.query(ProviderConfig)
        .filter(
            ProviderConfig.provider_id == provider.id,
            ProviderConfig.product_id == product.id,
        )
        .first()
    )
    if existing is not None:
        return

    session.add(
        ProviderConfig(
            provider_id=provider.id,
            product_id=product.id,
            version=1,
            is_active=True,
            config_json=default_payload().model_dump(mode="json"),
            notes="Initial seed (matches pre-DB hardcoded defaults).",
        )
    )
    session.commit()


def _ensure_sample_quotes(
    session: Session, provider: Provider, product: Product
) -> None:
    existing = (
        session.query(QuoteSnapshot)
        .filter(
            QuoteSnapshot.provider_id == provider.id,
            QuoteSnapshot.product_id == product.id,
        )
        .first()
    )
    if existing is not None:
        return

    session.add_all(
        QuoteSnapshot(
            provider_id=provider.id,
            product_id=product.id,
            product_line=product.product_line,
            snapshot_date=_SAMPLE_SNAPSHOT_DATE,
            currency="EUR",
            is_active=True,
            source="seed",
            offer_details={"source": "seed"},
            **row,
        )
        for row in _SAMPLE_SNAPSHOTS
    )
    session.commit()


def _ensure_sample_documents(
    session: Session, provider: Provider, product: Product
) -> None:
    existing = (
        session.query(Document).filter(Document.document_type == "policy_guide").first()
    )
    if existing is not None:
        return

    guide_body = (
        "Foyer car insurance for Luxembourg private-use drivers includes basic, "
        "standard, and premium coverage tiers. Premiums vary by age band, "
        "vehicle value band, mileage band, claims history, and vehicle power. "
        "Deductibles are typically lower in premium tiers."
    )
    faq_body = (
        "Eligibility is determined from normalized quote bands. If a customer "
        "profile changes, a new quote request should be stored so future "
        "searches stay auditable and reproducible."
    )

    guide = Document(
        provider_id=provider.id,
        product_id=product.id,
        document_type="policy_guide",
        title="Foyer Car Insurance Guide",
        source_uri="seed://foyer/car/guide",
        language="en",
        status="active",
        metadata_={"source": "seed"},
        body_text=guide_body,
    )
    faq = Document(
        provider_id=provider.id,
        product_id=product.id,
        document_type="policy_faq",
        title="Foyer Car Insurance FAQ",
        source_uri="seed://foyer/car/faq",
        language="en",
        status="active",
        metadata_={"source": "seed"},
        body_text=faq_body,
    )
    session.add_all([guide, faq])
    session.flush()

    # Two chunks from the guide (one sentence each), one from the FAQ.
    chunk_texts = [
        (guide, 1, guide_body.split(". ")[0] + "."),
        (guide, 2, ". ".join(guide_body.split(". ")[1:])),
        (faq, 1, faq_body),
    ]
    session.add_all(
        DocumentChunk(
            document_id=doc.id,
            chunk_index=idx,
            chunk_text=text,
            token_count=len(text.split()),
            metadata_={"source": "seed"},
            embedding=_embedding_provider.embed(text),
        )
        for (doc, idx, text) in chunk_texts
    )
    session.commit()


def main() -> None:
    session = SessionLocal()
    try:
        seed_data(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
