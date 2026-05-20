"""Inquiry normalization.

Previously held hardcoded band thresholds. They now live in
`app.services.provider_config` (and ultimately the `provider_configs`
table when seeded), but the *signature* of `normalize_car_inquiry` is
unchanged so existing callers (and the existing tests) keep working —
the second `bands` argument is optional and defaults to the same
thresholds the engine has always used.
"""

from __future__ import annotations

from app.schemas.common import ProductLine
from app.schemas.provider_config import BandConfig, BandThreshold
from app.schemas.recommend import CarInsuranceInquiry, InquiryBands, NormalizedInquiry


def normalize_car_inquiry(
    inquiry: CarInsuranceInquiry,
    bands: BandConfig | None = None,
) -> NormalizedInquiry:
    """Bucket an inquiry's continuous fields into bands.

    Pass `bands` from the DB-driven config to use operator-controlled
    thresholds; omit to fall back to the hardcoded defaults.
    """

    cfg = bands or _default_bands()
    inquiry_bands = InquiryBands(
        age_band=_pick_band(cfg.age, inquiry.driver_age),
        vehicle_value_band=_pick_band(cfg.vehicle_value, inquiry.vehicle_value_eur),
        mileage_band=_pick_band(cfg.mileage, inquiry.annual_mileage_km),
        claims_band=_pick_band(cfg.claims, inquiry.claims_last_5y),
        power_band=_pick_band(cfg.power, inquiry.vehicle_power_kw),
    )
    return NormalizedInquiry(
        product_line=ProductLine.car,
        country=inquiry.country,
        usage_type=inquiry.usage_type,
        coverage_preference=inquiry.coverage_preference,
        bands=inquiry_bands,
    )


def _pick_band(thresholds: list[BandThreshold], value: int) -> str:
    """Walk thresholds in order; return the first label whose max ≥ value.

    The Pydantic validator on `BandConfig` guarantees exactly one
    catch-all (max=None) entry, last, so this loop always returns.
    """

    for threshold in thresholds:
        if threshold.max is None or value <= threshold.max:
            return threshold.label
    # Defensive: BandConfig validator should make this unreachable.
    raise ValueError("Band thresholds missing catch-all entry")


def _default_bands() -> BandConfig:
    # Lazy import keeps normalization importable without the DB-config
    # module being fully loaded (matters for test bootstrap order).
    from app.services.provider_config import default_payload

    return default_payload().bands
