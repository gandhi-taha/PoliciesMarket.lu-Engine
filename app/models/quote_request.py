from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QuoteRequest(Base):
    __tablename__ = "quote_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_line: Mapped[str] = mapped_column(String(50), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    raw_inquiry: Mapped[dict] = mapped_column(JSONB, nullable=False)
    normalized_bands: Mapped[dict] = mapped_column(JSONB, nullable=False)
    coverage_preference: Mapped[str] = mapped_column(String(20), nullable=False)
    driver_age: Mapped[int] = mapped_column(Integer, nullable=False)
    years_licensed: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_value_eur: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_power_kw: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_age_years: Mapped[int] = mapped_column(Integer, nullable=False)
    annual_mileage_km: Mapped[int] = mapped_column(Integer, nullable=False)
    claims_last_5y: Mapped[int] = mapped_column(Integer, nullable=False)
    usage_type: Mapped[str] = mapped_column(String(20), nullable=False)
    age_band: Mapped[str] = mapped_column(String(20), nullable=False)
    vehicle_value_band: Mapped[str] = mapped_column(String(20), nullable=False)
    mileage_band: Mapped[str] = mapped_column(String(20), nullable=False)
    claims_band: Mapped[str] = mapped_column(String(10), nullable=False)
    power_band: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
