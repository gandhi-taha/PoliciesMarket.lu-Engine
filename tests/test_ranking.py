from dataclasses import dataclass

from app.schemas.common import CoverageLevel
from app.services.ranking import rank_snapshots


@dataclass
class SnapshotStub:
    coverage_level: str
    annual_premium_cents: int


def test_rank_snapshots() -> None:
    snapshots = [
        SnapshotStub(coverage_level="basic", annual_premium_cents=52000),
        SnapshotStub(coverage_level="standard", annual_premium_cents=70000),
        SnapshotStub(coverage_level="standard", annual_premium_cents=65000),
        SnapshotStub(coverage_level="premium", annual_premium_cents=90000),
    ]

    ranked = rank_snapshots(snapshots, CoverageLevel.standard)

    assert ranked.best_match.annual_premium_cents == 65000
    assert ranked.lower_cost_option.coverage_level == "basic"
    assert ranked.higher_cover_option.coverage_level == "premium"
    assert ranked.all_matching_offers[0] == ranked.best_match
