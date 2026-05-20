from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import CoverageLevel, ProductLine, UsageType
from app.schemas.recommend import InquiryBands


class PartialCarInsuranceInquiry(BaseModel):
    country: str | None = Field(default=None, min_length=2, max_length=2)
    driver_age: int | None = Field(default=None, ge=18, le=100)
    years_licensed: int | None = Field(default=None, ge=0, le=80)
    vehicle_value_eur: int | None = Field(default=None, ge=1000, le=300000)
    vehicle_power_kw: int | None = Field(default=None, ge=30, le=400)
    vehicle_age_years: int | None = Field(default=None, ge=0, le=30)
    annual_mileage_km: int | None = Field(default=None, ge=0, le=60000)
    claims_last_5y: int | None = Field(default=None, ge=0, le=10)
    usage_type: UsageType | None = None
    coverage_preference: CoverageLevel | None = None

    model_config = ConfigDict(extra="forbid")


class QueryParseRequest(BaseModel):
    free_text: str = Field(..., min_length=1)
    structured: PartialCarInsuranceInquiry | None = None

    model_config = ConfigDict(extra="forbid")


class NormalizedFilters(BaseModel):
    product_line: ProductLine | None = None
    country: str | None = None
    usage_type: UsageType | None = None
    coverage_preference: CoverageLevel | None = None
    bands: InquiryBands | None = None

    model_config = ConfigDict(extra="forbid")


class QueryParseResponse(BaseModel):
    filters: NormalizedFilters
    missing_fields: list[str]
    prompt_version: str

    model_config = ConfigDict(extra="forbid")
