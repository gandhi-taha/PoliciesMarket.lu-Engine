"""Recommendation service.

Two execution modes, picked by the caller:

- **Single-provider** (`recommend_for_inquiry`): the original flow. The
  recommend route uses this when the client passes `?provider=<code>`.
- **Multi-provider** (`recommend_for_inquiry_multi`): fan out across
  every enabled adapter from `settings.providers`, merge their offers,
  and rank globally. The recommend route uses this when no explicit
  provider is supplied — that's how `/v1/recommend` returns LALUX +
  Bâloise + AXA + Foyer offers in one call.

Both modes share the same rating-engine fallback: if an adapter returns
no exact-band match for a coverage level, the rating engine tries to
synthesize an estimated offer from the insurer's collected snapshots
(per planning doc §531-533). Estimated offers carry `source='estimated'`
and a confidence < 1.0, so the UI can badge them as such.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.models import QuoteRequest, Recommendation
from app.providers.base import ProviderAdapter
from app.providers.registry import iter_enabled_adapters
from app.schemas.common import CoverageLevel, ProductLine
from app.schemas.recommend import (
    CarInsuranceInquiry,
    NormalizedInquiry,
    QuoteSnapshotOut,
    RecommendationResponse,
)
from app.services.normalization import normalize_car_inquiry
from app.services.provider_config import load_active_config
from app.services.ranking import rank_snapshots
from app.services.rating_engine import rating_engine


# Coverage levels we ask the rating engine to estimate for, when an
# adapter returns nothing. Ordered so the user's preferred level is
# attempted first and the others fill in alternatives.
ALL_COVERAGE_LEVELS = ("basic", "standard", "premium")


def recommend_for_inquiry(
    session: Session, inquiry: CarInsuranceInquiry, adapter: ProviderAdapter
) -> RecommendationResponse:
    """Single-provider recommendation — preserves the original contract.

    Used when `/v1/recommend?provider=foyer` is called explicitly.
    """

    normalized = _normalize(session, inquiry, adapter)
    provider = adapter.get_provider(session)

    snapshots = _collect_snapshots_with_fallback(session, adapter, normalized, inquiry)
    ranked = rank_snapshots(snapshots, inquiry.coverage_preference)

    quote_request = _persist_quote_request(session, inquiry, normalized)
    _persist_recommendation(session, quote_request, provider, ranked)
    session.commit()

    return RecommendationResponse(
        request_id=quote_request.id,
        provider_code=provider.code,
        best_match=_to_snapshot_out(ranked.best_match) if ranked.best_match else None,
        lower_cost_option=(
            _to_snapshot_out(ranked.lower_cost_option) if ranked.lower_cost_option else None
        ),
        higher_cover_option=(
            _to_snapshot_out(ranked.higher_cover_option) if ranked.higher_cover_option else None
        ),
        all_matching_offers=[_to_snapshot_out(snap) for snap in ranked.all_matching_offers],
    )


def recommend_for_inquiry_multi(
    session: Session, inquiry: CarInsuranceInquiry
) -> RecommendationResponse:
    """Multi-provider recommendation: fan out across every enabled adapter.

    Order of operations per adapter:
    1. Ask the adapter for exact-band snapshot matches (snapshot mode
       hits the DB; live mode hits the vendor feed).
    2. If a coverage level has no exact match, ask the rating engine to
       synthesize one from the insurer's collected snapshots.
    3. Merge everything, rank globally by coverage preference, and
       persist a single `Recommendation` row labelled with the
       cross-provider best match.

    Failures in one adapter don't kill the whole request — they're
    skipped and logged in `offer_details` of the response (each adapter
    is wrapped in try/except so a flaky live-mode adapter never breaks
    snapshot-mode providers next to it).
    """

    all_snapshots: list[Any] = []
    provider_for_audit = None

    # We need *some* (provider, product) to feed `load_active_config` for
    # band normalization. The first enabled adapter's pair is fine; bands
    # are largely the same across insurers and any per-insurer override
    # would have to be loaded inside the rating engine anyway.
    adapters = list(iter_enabled_adapters())
    if not adapters:
        raise LookupError(
            "No enabled providers in settings.providers; cannot recommend."
        )

    first_adapter = adapters[0]
    normalized = _normalize(session, inquiry, first_adapter)

    for adapter in adapters:
        try:
            snapshots = _collect_snapshots_with_fallback(
                session, adapter, normalized, inquiry
            )
        except Exception:  # noqa: BLE001 — adapter resilience boundary
            # An adapter blowing up shouldn't take the whole response
            # down. Skip and continue. The remaining adapters still
            # return their offers.
            continue
        all_snapshots.extend(snapshots)
        if provider_for_audit is None:
            try:
                provider_for_audit = adapter.get_provider(session)
            except Exception:  # noqa: BLE001
                pass

    if provider_for_audit is None:
        provider_for_audit = first_adapter.get_provider(session)

    ranked = rank_snapshots(all_snapshots, inquiry.coverage_preference)

    quote_request = _persist_quote_request(session, inquiry, normalized)
    _persist_recommendation(session, quote_request, provider_for_audit, ranked)
    session.commit()

    return RecommendationResponse(
        request_id=quote_request.id,
        provider_code=provider_for_audit.code,
        best_match=_to_snapshot_out(ranked.best_match) if ranked.best_match else None,
        lower_cost_option=(
            _to_snapshot_out(ranked.lower_cost_option) if ranked.lower_cost_option else None
        ),
        higher_cover_option=(
            _to_snapshot_out(ranked.higher_cover_option) if ranked.higher_cover_option else None
        ),
        all_matching_offers=[_to_snapshot_out(snap) for snap in ranked.all_matching_offers],
    )


# ---- internals ---------------------------------------------------


def _normalize(
    session: Session, inquiry: CarInsuranceInquiry, adapter: ProviderAdapter
) -> NormalizedInquiry:
    provider_code = getattr(adapter, "provider_code", "foyer")
    product_code = getattr(adapter, "product_code", f"{provider_code}_car")
    active_cfg = load_active_config(session, provider_code, product_code)
    return normalize_car_inquiry(inquiry, bands=active_cfg.bands)


def _collect_snapshots_with_fallback(
    session: Session,
    adapter: ProviderAdapter,
    normalized: NormalizedInquiry,
    inquiry: CarInsuranceInquiry,
) -> list[Any]:
    """Exact-band snapshots from the adapter + rating-engine fallback.

    Rating engine is consulted only for coverage levels the adapter
    didn't already cover. Estimated offers are tagged `source='estimated'`
    so the UI surfaces them as model-derived rather than verified.
    """

    snapshots = list(adapter.find_matching_snapshots(session, normalized))
    covered_levels = {s.coverage_level for s in snapshots}
    missing_levels = [level for level in ALL_COVERAGE_LEVELS if level not in covered_levels]
    if not missing_levels:
        return snapshots

    try:
        provider = adapter.get_provider(session)
        product = adapter.get_product(session)
    except Exception:  # noqa: BLE001
        return snapshots

    for level in missing_levels:
        synthetic = rating_engine.synthesize(
            session, provider, product, normalized, level
        )
        if synthetic is not None:
            snapshots.append(synthetic)
    return snapshots


def _persist_quote_request(
    session: Session, inquiry: CarInsuranceInquiry, normalized: NormalizedInquiry
) -> QuoteRequest:
    quote_request = QuoteRequest(
        product_line=normalized.product_line.value,
        country=normalized.country,
        raw_inquiry=inquiry.model_dump(),
        normalized_bands=normalized.bands.model_dump(),
        coverage_preference=normalized.coverage_preference.value,
        driver_age=inquiry.driver_age,
        years_licensed=inquiry.years_licensed,
        vehicle_value_eur=inquiry.vehicle_value_eur,
        vehicle_power_kw=inquiry.vehicle_power_kw,
        vehicle_age_years=inquiry.vehicle_age_years,
        annual_mileage_km=inquiry.annual_mileage_km,
        claims_last_5y=inquiry.claims_last_5y,
        usage_type=normalized.usage_type.value,
        age_band=normalized.bands.age_band,
        vehicle_value_band=normalized.bands.vehicle_value_band,
        mileage_band=normalized.bands.mileage_band,
        claims_band=normalized.bands.claims_band,
        power_band=normalized.bands.power_band,
    )
    session.add(quote_request)
    session.flush()
    return quote_request


def _persist_recommendation(
    session: Session,
    quote_request: QuoteRequest,
    provider,
    ranked,
) -> None:
    # Synthetic (rating-engine) snapshots are not persisted in
    # `quote_snapshots`, so we only reference them in the
    # `all_matching_snapshot_ids` JSON (which accepts any UUID string)
    # but skip them when populating the FK columns. The FK columns can
    # only point at real rows.
    def _persisted_id(snap):
        if snap is None:
            return None
        if getattr(snap, "source", "manual") == "estimated":
            return None
        return snap.id

    recommendation = Recommendation(
        quote_request_id=quote_request.id,
        provider_id=provider.id,
        best_match_snapshot_id=_persisted_id(ranked.best_match),
        lower_cost_snapshot_id=_persisted_id(ranked.lower_cost_option),
        higher_cover_snapshot_id=_persisted_id(ranked.higher_cover_option),
        all_matching_snapshot_ids=[str(snap.id) for snap in ranked.all_matching_offers],
        ranking_version="v1",
    )
    session.add(recommendation)


def _to_snapshot_out(snapshot) -> QuoteSnapshotOut:
    # Source / confidence / valid_until are read with `getattr` so callers
    # passing a non-ORM stand-in (e.g. the rating engine's synthetic
    # snapshot dataclass) still produce valid output without crashing.
    return QuoteSnapshotOut(
        id=snapshot.id,
        provider_code=snapshot.provider.code,
        product_code=snapshot.product.code,
        product_line=ProductLine(snapshot.product_line),
        coverage_level=snapshot.coverage_level,
        currency=snapshot.currency,
        annual_premium_cents=snapshot.annual_premium_cents,
        deductible_cents=snapshot.deductible_cents,
        snapshot_date=snapshot.snapshot_date,
        usage_type=snapshot.usage_type,
        age_band=snapshot.age_band,
        vehicle_value_band=snapshot.vehicle_value_band,
        mileage_band=snapshot.mileage_band,
        claims_band=snapshot.claims_band,
        power_band=snapshot.power_band,
        source=getattr(snapshot, "source", "manual"),
        confidence=getattr(snapshot, "confidence", 1),
        valid_until=getattr(snapshot, "valid_until", None),
    )
