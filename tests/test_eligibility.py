from dataclasses import dataclass

from app.schemas.common import CoverageLevel, ProductLine, UsageType
from app.schemas.recommend import InquiryBands, NormalizedInquiry
from app.services.eligibility import snapshot_matches


@dataclass
class SnapshotStub:
    age_band: str
    vehicle_value_band: str
    mileage_band: str
    claims_band: str
    power_band: str
    usage_type: str


def test_snapshot_matches_true() -> None:
    normalized = NormalizedInquiry(
        product_line=ProductLine.car,
        country="LU",
        usage_type=UsageType.private,
        coverage_preference=CoverageLevel.standard,
        bands=InquiryBands(
            age_band="25-34",
            vehicle_value_band="15001-30000",
            mileage_band="8001-15000",
            claims_band="0",
            power_band="81-110",
        ),
    )

    snapshot = SnapshotStub(
        age_band="25-34",
        vehicle_value_band="15001-30000",
        mileage_band="8001-15000",
        claims_band="0",
        power_band="81-110",
        usage_type="private",
    )

    assert snapshot_matches(normalized, snapshot) is True


def test_snapshot_matches_false() -> None:
    normalized = NormalizedInquiry(
        product_line=ProductLine.car,
        country="LU",
        usage_type=UsageType.private,
        coverage_preference=CoverageLevel.standard,
        bands=InquiryBands(
            age_band="25-34",
            vehicle_value_band="15001-30000",
            mileage_band="8001-15000",
            claims_band="0",
            power_band="81-110",
        ),
    )

    snapshot = SnapshotStub(
        age_band="35-49",
        vehicle_value_band="15001-30000",
        mileage_band="8001-15000",
        claims_band="0",
        power_band="81-110",
        usage_type="private",
    )

    assert snapshot_matches(normalized, snapshot) is False
