from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import CoverageLevel, ProductLine, UsageType


# Mirrors the CHECK constraint in 0005_add_quote_snapshot_provenance.py.
SnapshotSource = Literal["manual", "scraper", "partner_feed", "estimated", "seed"]


class CarInsuranceInquiry(BaseModel):
    country: str = Field(default="LU", min_length=2, max_length=2)
    driver_age: int = Field(..., ge=18, le=100)
    years_licensed: int = Field(..., ge=0, le=80)
    vehicle_value_eur: int = Field(..., ge=1000, le=300000)
    vehicle_power_kw: int = Field(..., ge=30, le=400)
    vehicle_age_years: int = Field(..., ge=0, le=30)
    annual_mileage_km: int = Field(..., ge=0, le=60000)
    claims_last_5y: int = Field(..., ge=0, le=10)
    usage_type: UsageType = UsageType.private
    coverage_preference: CoverageLevel = CoverageLevel.standard


class InquiryBands(BaseModel):
    age_band: str
    vehicle_value_band: str
    mileage_band: str
    claims_band: str
    power_band: str


class NormalizedInquiry(BaseModel):
    product_line: ProductLine
    country: str
    usage_type: UsageType
    coverage_preference: CoverageLevel
    bands: InquiryBands


class QuoteSnapshotOut(BaseModel):
    id: UUID
    provider_code: str
    product_code: str
    product_line: ProductLine
    coverage_level: CoverageLevel
    currency: str
    annual_premium_cents: int
    deductible_cents: int
    snapshot_date: date
    usage_type: UsageType
    age_band: str
    vehicle_value_band: str
    mileage_band: str
    claims_band: str
    power_band: str
    # Provenance — lets the UI badge results honestly (verified snapshot
    # date vs rating-engine estimate) per plan §3224.
    source: SnapshotSource = "manual"
    confidence: Decimal = Field(default=Decimal("1.000"), ge=0, le=1)
    valid_until: date | None = None

    model_config = ConfigDict(from_attributes=True)


class RecommendationResponse(BaseModel):
    request_id: UUID
    provider_code: str
    best_match: QuoteSnapshotOut | None
    lower_cost_option: QuoteSnapshotOut | None
    higher_cover_option: QuoteSnapshotOut | None
    all_matching_offers: list[QuoteSnapshotOut]
