from __future__ import annotations

import uuid
from datetime import date, datetime

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class QuoteSnapshot(Base):
    __tablename__ = "quote_snapshots"
    __table_args__ = (
        Index("ix_quote_snapshots_provider_product", "provider_id", "product_id"),
        Index(
            "ix_quote_snapshots_bands",
            "age_band",
            "vehicle_value_band",
            "mileage_band",
            "claims_band",
            "power_band",
            "usage_type",
        ),
        Index("ix_quote_snapshots_provider_source", "provider_id", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    product_line: Mapped[str] = mapped_column(String(50), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_level: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    annual_premium_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    deductible_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    age_band: Mapped[str] = mapped_column(String(20), nullable=False)
    vehicle_value_band: Mapped[str] = mapped_column(String(20), nullable=False)
    mileage_band: Mapped[str] = mapped_column(String(20), nullable=False)
    claims_band: Mapped[str] = mapped_column(String(10), nullable=False)
    power_band: Mapped[str] = mapped_column(String(20), nullable=False)
    usage_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Provenance — see alembic/versions/0005_add_quote_snapshot_provenance.py
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default="seed"
    )
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("1.000"), server_default="1.000"
    )
    offer_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    provider: Mapped["Provider"] = relationship("Provider")
    product: Mapped["Product"] = relationship("Product")
