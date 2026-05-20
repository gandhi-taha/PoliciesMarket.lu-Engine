from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from app.schemas.common import CoverageLevel

COVERAGE_RANK = {
    "basic": 1,
    "standard": 2,
    "premium": 3,
}


class SnapshotForRanking(Protocol):
    coverage_level: str
    annual_premium_cents: int


@dataclass
class RankedOffers:
    best_match: SnapshotForRanking | None
    lower_cost_option: SnapshotForRanking | None
    higher_cover_option: SnapshotForRanking | None
    all_matching_offers: list[SnapshotForRanking]


def rank_snapshots(
    snapshots: Sequence[SnapshotForRanking],
    preferred_coverage: CoverageLevel | str,
) -> RankedOffers:
    if not snapshots:
        return RankedOffers(None, None, None, [])

    preferred_value = (
        preferred_coverage.value
        if isinstance(preferred_coverage, CoverageLevel)
        else str(preferred_coverage)
    )
    preferred_rank = _coverage_rank(preferred_value)

    sorted_offers = sorted(snapshots, key=lambda s: _sort_key(s, preferred_rank))
    best_match = sorted_offers[0]

    lower_cost_option = _pick_lower_cost(sorted_offers, best_match, preferred_rank)
    higher_cover_option = _pick_higher_cover(sorted_offers, best_match, preferred_rank)

    return RankedOffers(best_match, lower_cost_option, higher_cover_option, list(sorted_offers))


def _coverage_rank(level: str) -> int:
    return COVERAGE_RANK.get(level, 0)


def _sort_key(snapshot: SnapshotForRanking, preferred_rank: int) -> tuple[int, int, int, int]:
    rank = _coverage_rank(snapshot.coverage_level)
    prefer_or_higher = 0 if rank >= preferred_rank else 1
    distance = abs(rank - preferred_rank)
    return (prefer_or_higher, distance, snapshot.annual_premium_cents, -rank)


def _pick_lower_cost(
    snapshots: Sequence[SnapshotForRanking],
    best_match: SnapshotForRanking,
    preferred_rank: int,
) -> SnapshotForRanking | None:
    lower_candidates = [
        snap for snap in snapshots if snap.annual_premium_cents < best_match.annual_premium_cents
    ]
    if not lower_candidates:
        return None
    return min(
        lower_candidates,
        key=lambda s: (
            s.annual_premium_cents,
            abs(_coverage_rank(s.coverage_level) - preferred_rank),
            -_coverage_rank(s.coverage_level),
        ),
    )


def _pick_higher_cover(
    snapshots: Sequence[SnapshotForRanking],
    best_match: SnapshotForRanking,
    preferred_rank: int,
) -> SnapshotForRanking | None:
    best_rank = _coverage_rank(best_match.coverage_level)
    higher_candidates = [
        snap for snap in snapshots if _coverage_rank(snap.coverage_level) > best_rank
    ]
    if not higher_candidates:
        return None
    return min(
        higher_candidates,
        key=lambda s: (
            s.annual_premium_cents,
            abs(_coverage_rank(s.coverage_level) - preferred_rank),
            -_coverage_rank(s.coverage_level),
        ),
    )
