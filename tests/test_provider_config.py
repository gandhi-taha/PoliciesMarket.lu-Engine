"""Tests for the DB-driven provider config payload + default behavior."""

from __future__ import annotations

import pytest

from app.schemas.common import CoverageLevel, UsageType
from app.schemas.provider_config import (
    BandConfig,
    BandThreshold,
    ProviderConfigPayload,
)
from app.schemas.recommend import CarInsuranceInquiry
from app.services.normalization import normalize_car_inquiry
from app.services.provider_config import default_payload, load_active_config


def _inquiry() -> CarInsuranceInquiry:
    return CarInsuranceInquiry(
        country="LU",
        driver_age=32,
        years_licensed=12,
        vehicle_value_eur=22000,
        vehicle_power_kw=95,
        vehicle_age_years=4,
        annual_mileage_km=12000,
        claims_last_5y=0,
        usage_type=UsageType.private,
        coverage_preference=CoverageLevel.standard,
    )


def test_default_payload_matches_legacy_bands() -> None:
    """Default config must mirror the pre-DB hardcoded thresholds.

    A 32-year-old in a 22k car with 12k km should land in the same
    bands the legacy normalizer used to produce."""

    cfg = default_payload()
    normalized = normalize_car_inquiry(_inquiry(), bands=cfg.bands)
    b = normalized.bands
    assert b.age_band == "25-34"
    assert b.vehicle_value_band == "15001-30000"
    assert b.mileage_band == "8001-15000"
    assert b.claims_band == "0"
    assert b.power_band == "81-110"


def test_normalize_without_bands_arg_falls_back_to_defaults() -> None:
    """Existing callers that don't pass `bands` get the same behavior."""

    normalized = normalize_car_inquiry(_inquiry())
    assert normalized.bands.age_band == "25-34"


def test_custom_bands_change_bucketing() -> None:
    """A custom config should change which band a value falls into."""

    custom = BandConfig(
        age=[
            BandThreshold(max=29, label="young"),
            BandThreshold(max=49, label="middle"),
            BandThreshold(max=None, label="senior"),
        ],
        vehicle_value=default_payload().bands.vehicle_value,
        mileage=default_payload().bands.mileage,
        claims=default_payload().bands.claims,
        power=default_payload().bands.power,
    )
    normalized = normalize_car_inquiry(_inquiry(), bands=custom)
    assert normalized.bands.age_band == "middle"


def test_bandconfig_requires_catch_all() -> None:
    with pytest.raises(ValueError):
        BandConfig(
            age=[BandThreshold(max=10, label="a"), BandThreshold(max=20, label="b")],
        )


def test_bandconfig_catch_all_must_be_last() -> None:
    with pytest.raises(ValueError):
        BandConfig(
            age=[BandThreshold(max=None, label="all"), BandThreshold(max=10, label="a")],
        )


def test_load_active_config_no_session_returns_defaults() -> None:
    """The query parser pipeline can call load_active_config with no session."""

    cfg = load_active_config(None, "foyer", "foyer_car")
    assert isinstance(cfg, ProviderConfigPayload)
    assert cfg.bands.age[0].label == "18-24"
