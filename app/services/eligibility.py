from typing import Protocol

from app.schemas.recommend import NormalizedInquiry


class SnapshotEligibility(Protocol):
    age_band: str
    vehicle_value_band: str
    mileage_band: str
    claims_band: str
    power_band: str
    usage_type: str


def snapshot_matches(normalized: NormalizedInquiry, snapshot: SnapshotEligibility) -> bool:
    return (
        snapshot.age_band == normalized.bands.age_band
        and snapshot.vehicle_value_band == normalized.bands.vehicle_value_band
        and snapshot.mileage_band == normalized.bands.mileage_band
        and snapshot.claims_band == normalized.bands.claims_band
        and snapshot.power_band == normalized.bands.power_band
        and snapshot.usage_type == normalized.usage_type.value
    )


def filter_eligible_snapshots(
    normalized: NormalizedInquiry, snapshots: list[SnapshotEligibility]
) -> list[SnapshotEligibility]:
    return [snapshot for snapshot in snapshots if snapshot_matches(normalized, snapshot)]
