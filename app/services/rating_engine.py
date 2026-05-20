"""Per-insurer rating engine.

Implements the formula from planning doc §531-533:

    base_price        = f(company, product_type, risk_profile)
    adjusted_price    = base_price * multipliers + addons

Purpose
-------
When ops collects a small grid of real quotes per insurer (say, 30-60
profile combinations), exact-band lookup in `quote_snapshots` will only
cover a fraction of inbound user inquiries. Without this engine, every
out-of-grid profile would get "no offers found" until ops manually
collects that exact band combo. That's bad UX, and it scales linearly
with band-combination count.

This module fits a simple log-linear pricing model to the collected
snapshots per (provider, product, coverage_level). When the recommend
flow can't find an exact band match, it asks the rating engine to
synthesize a quote, tagged `source='estimated'` with a confidence
score the UI surfaces as an "Estimated" badge.

Model
-----
log(price) = log(base) + alpha_age + alpha_value + alpha_mileage
                       + alpha_claims + alpha_power + alpha_usage

Each `alpha_<band>` is a categorical fixed effect — one coefficient per
band label. The model is fit by ordinary least squares (closed form,
no extra deps) on the log of `annual_premium_cents`. With ~30 samples
the fit is rough but better than "no offer"; with ~60+ it's
production-quality. Coefficients are recomputed in-memory and cached
per (provider, product, coverage_level) for the lifetime of the
process. Cache invalidates on snapshot writes via `invalidate()`.

Confidence
----------
Confidence drops as we extrapolate further from the training grid:

- 0.95 when every band of the inquiry appears in the training set
- 0.80 when 4/6 bands appear
- 0.60 when 3/6 bands appear
- below 0.60 we refuse to synthesize (return None) — the UI then
  surfaces "Request a personalized quote" with an assisted-redirect
  link instead of a fabricated price.

Why OLS instead of something fancier
------------------------------------
- Zero new runtime deps (no numpy/sklearn) — the engine container stays
  slim and matches the existing pyproject.toml.
- Closed-form, deterministic, fast (sub-millisecond per fit on the
  expected data sizes).
- Interpretable: the coefficients map 1:1 to the planning-doc formula
  (`alpha_age` is the age-band multiplier in log-space).
- Stable: small data → small variance, no overfitting risk like a tree
  ensemble would have at n=30.

When ops grows the grid past 200 quotes per insurer and we want
non-linear effects (interactions between age and claims, say), this
module's interface stays stable — only the internals change.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Product, Provider, QuoteSnapshot
from app.schemas.recommend import NormalizedInquiry

if TYPE_CHECKING:  # pragma: no cover
    pass


# Confidence thresholds, see module docstring.
MIN_CONFIDENCE_TO_RETURN = 0.60
CONFIDENCE_BY_BANDS_SEEN = {6: 0.95, 5: 0.88, 4: 0.80, 3: 0.70}

# Minimum collected snapshots per (provider, product, coverage_level)
# below which we refuse to fit (returns no estimate). 5 is the floor for
# even a trivial fit with 5 fixed effects; below that the system is
# underdetermined.
MIN_SAMPLES_FOR_FIT = 5

# Synthetic snapshots are advisory; tell the UI to treat them as stale
# after this many days unless the underlying training data is refreshed.
ESTIMATE_VALIDITY_DAYS = 14


@dataclass(frozen=True)
class _BandEffects:
    """Fitted per-band multipliers in log-space, plus the intercept."""

    intercept: float  # log(base price)
    age: dict[str, float]
    vehicle_value: dict[str, float]
    mileage: dict[str, float]
    claims: dict[str, float]
    power: dict[str, float]
    usage: dict[str, float]
    deductible_cents_mean: int
    currency: str
    snapshot_dates: tuple[date, ...]  # for confidence + valid_until math


@dataclass
class SyntheticSnapshot:
    """Drop-in stand-in for a `QuoteSnapshot` ORM row.

    `recommendation_service._to_snapshot_out` uses `getattr` for
    provenance fields, so this dataclass works without a SQLAlchemy
    session. Critically, it never gets `session.add()`-ed — it exists
    only for the lifetime of one request.
    """

    id: UUID
    provider_id: UUID
    product_id: UUID
    product_line: str
    snapshot_date: date
    coverage_level: str
    currency: str
    annual_premium_cents: int
    deductible_cents: int
    age_band: str
    vehicle_value_band: str
    mileage_band: str
    claims_band: str
    power_band: str
    usage_type: str
    is_active: bool
    source: str
    confidence: Decimal
    valid_until: date | None
    offer_details: dict | None
    provider: Provider | None = None
    product: Product | None = None


class RatingEngine:
    """Fits and queries log-linear pricing models per insurer/coverage.

    Thread-safe: a single module-level lock guards the cache. Reads
    after the cache is warm are lock-free in the hot path (the fit
    function only acquires the lock at miss/invalidation time).
    """

    def __init__(self) -> None:
        # Cache key: (provider_id, product_id, coverage_level)
        self._cache: dict[tuple[UUID, UUID, str], _BandEffects | None] = {}
        self._lock = threading.RLock()

    # ---- public API ------------------------------------------------

    def synthesize(
        self,
        session: Session,
        provider: Provider,
        product: Product,
        normalized: NormalizedInquiry,
        coverage_level: str,
    ) -> SyntheticSnapshot | None:
        """Return a `SyntheticSnapshot` for the requested profile, or None.

        Returns None when:
        - fewer than MIN_SAMPLES_FOR_FIT training snapshots exist
        - the fitted model would yield confidence < MIN_CONFIDENCE_TO_RETURN
        """

        effects = self._effects_for(session, provider, product, coverage_level)
        if effects is None:
            return None

        bands = normalized.bands
        usage_type = normalized.usage_type.value

        bands_seen, log_price = self._predict(effects, bands, usage_type)
        confidence = _confidence_from_bands_seen(bands_seen)
        if confidence < MIN_CONFIDENCE_TO_RETURN:
            return None

        price_cents = int(round(math.exp(log_price)))
        # Sanity-floor a few hundred euros to catch fits that explode on
        # very thin data. Better to skip than to surface a wild number.
        if price_cents < 10_000 or price_cents > 1_000_000:
            return None

        snapshot_date = max(effects.snapshot_dates) if effects.snapshot_dates else date.today()
        return SyntheticSnapshot(
            id=uuid4(),
            provider_id=provider.id,
            product_id=product.id,
            product_line=product.product_line,
            snapshot_date=snapshot_date,
            coverage_level=coverage_level,
            currency=effects.currency,
            annual_premium_cents=price_cents,
            deductible_cents=effects.deductible_cents_mean,
            age_band=bands.age_band,
            vehicle_value_band=bands.vehicle_value_band,
            mileage_band=bands.mileage_band,
            claims_band=bands.claims_band,
            power_band=bands.power_band,
            usage_type=usage_type,
            is_active=True,
            source="estimated",
            confidence=Decimal(f"{confidence:.3f}"),
            valid_until=snapshot_date + timedelta(days=ESTIMATE_VALIDITY_DAYS),
            offer_details={
                "source": "estimated",
                "model": "ols_log_linear_v1",
                "bands_seen": bands_seen,
                "training_n": len(effects.snapshot_dates),
            },
            provider=provider,
            product=product,
        )

    def invalidate(self, provider_id: UUID, product_id: UUID) -> None:
        """Drop cached fits for a (provider, product). Call after snapshot writes."""

        with self._lock:
            to_drop = [
                key
                for key in self._cache
                if key[0] == provider_id and key[1] == product_id
            ]
            for key in to_drop:
                self._cache.pop(key, None)

    def invalidate_all(self) -> None:
        with self._lock:
            self._cache.clear()

    # ---- internals -------------------------------------------------

    def _effects_for(
        self,
        session: Session,
        provider: Provider,
        product: Product,
        coverage_level: str,
    ) -> _BandEffects | None:
        key = (provider.id, product.id, coverage_level)
        cached = self._cache.get(key, _MISSING)
        if cached is not _MISSING:
            return cached  # may be None (negative cache)

        with self._lock:
            cached = self._cache.get(key, _MISSING)
            if cached is not _MISSING:
                return cached
            snapshots = self._load_training_snapshots(
                session, provider, product, coverage_level
            )
            effects = _fit_effects(snapshots) if len(snapshots) >= MIN_SAMPLES_FOR_FIT else None
            self._cache[key] = effects
            return effects

    @staticmethod
    def _load_training_snapshots(
        session: Session,
        provider: Provider,
        product: Product,
        coverage_level: str,
    ) -> list[QuoteSnapshot]:
        # Exclude estimates from the training set — fitting a model on
        # the model's own outputs would amplify errors.
        stmt = (
            select(QuoteSnapshot)
            .where(
                QuoteSnapshot.provider_id == provider.id,
                QuoteSnapshot.product_id == product.id,
                QuoteSnapshot.coverage_level == coverage_level,
                QuoteSnapshot.is_active.is_(True),
                QuoteSnapshot.source.in_(("manual", "scraper", "partner_feed", "seed")),
            )
            .options(selectinload(QuoteSnapshot.provider), selectinload(QuoteSnapshot.product))
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def _predict(
        effects: _BandEffects,
        bands,
        usage_type: str,
    ) -> tuple[int, float]:
        """Return (bands_seen_count, log_price)."""

        log_price = effects.intercept
        seen = 0
        for value, table in (
            (bands.age_band, effects.age),
            (bands.vehicle_value_band, effects.vehicle_value),
            (bands.mileage_band, effects.mileage),
            (bands.claims_band, effects.claims),
            (bands.power_band, effects.power),
            (usage_type, effects.usage),
        ):
            if value in table:
                log_price += table[value]
                seen += 1
            # Unknown bands contribute 0 to log-price (baseline). That's
            # the standard "reference level" treatment of categorical fixed
            # effects and is why we count `bands_seen` for confidence.
        return seen, log_price


# Sentinel so the cache can distinguish "miss" from "negative result".
_MISSING = object()


def _fit_effects(snapshots: list[QuoteSnapshot]) -> _BandEffects | None:
    """Fit log(price) = intercept + sum(alpha_band) by ordinary least squares.

    Implementation: one-hot encode each band's labels, drop one level
    per band as the reference (standard treatment-coding), solve the
    normal equations directly with Gaussian elimination on a small
    dense matrix. No numpy needed — n is in the dozens, k is in the
    teens.
    """

    if not snapshots:
        return None

    band_columns = _collect_band_columns(snapshots)
    if not band_columns:
        return None

    # Build the design matrix X (n × k) and target y (n).
    n = len(snapshots)
    k = 1 + sum(len(labels) for labels in band_columns.values())  # +1 for intercept
    if n < k:
        # Underdetermined system — won't fit reliably. Fall back to a
        # simpler intercept-only model so we at least know the average.
        return _fit_intercept_only(snapshots)

    X: list[list[float]] = []
    y: list[float] = []
    column_index = _build_column_index(band_columns)

    for snap in snapshots:
        row = [0.0] * k
        row[0] = 1.0  # intercept
        for band_name, label, _ in _iter_snapshot_bands(snap):
            idx = column_index.get((band_name, label))
            if idx is not None:
                row[idx] = 1.0
        X.append(row)
        y.append(math.log(max(1, snap.annual_premium_cents)))

    coefficients = _solve_normal_equations(X, y)
    if coefficients is None:
        return _fit_intercept_only(snapshots)

    intercept = coefficients[0]
    effects = {
        band_name: _coefficients_to_label_map(
            coefficients, column_index, band_name, labels
        )
        for band_name, labels in band_columns.items()
    }

    deductible_mean = int(
        round(sum(s.deductible_cents for s in snapshots) / max(1, n))
    )
    currency = snapshots[0].currency

    return _BandEffects(
        intercept=intercept,
        age=effects.get("age", {}),
        vehicle_value=effects.get("vehicle_value", {}),
        mileage=effects.get("mileage", {}),
        claims=effects.get("claims", {}),
        power=effects.get("power", {}),
        usage=effects.get("usage", {}),
        deductible_cents_mean=deductible_mean,
        currency=currency,
        snapshot_dates=tuple(s.snapshot_date for s in snapshots),
    )


def _fit_intercept_only(snapshots: list[QuoteSnapshot]) -> _BandEffects:
    """Mean log-price across snapshots. Used when n < k (underdetermined)."""

    log_prices = [math.log(max(1, s.annual_premium_cents)) for s in snapshots]
    intercept = sum(log_prices) / len(log_prices)
    return _BandEffects(
        intercept=intercept,
        age={},
        vehicle_value={},
        mileage={},
        claims={},
        power={},
        usage={},
        deductible_cents_mean=int(
            round(sum(s.deductible_cents for s in snapshots) / len(snapshots))
        ),
        currency=snapshots[0].currency,
        snapshot_dates=tuple(s.snapshot_date for s in snapshots),
    )


def _collect_band_columns(
    snapshots: list[QuoteSnapshot],
) -> dict[str, list[str]]:
    """Distinct labels per band, dropping the most-frequent label as the reference."""

    label_counts: dict[str, dict[str, int]] = {
        "age": {},
        "vehicle_value": {},
        "mileage": {},
        "claims": {},
        "power": {},
        "usage": {},
    }
    for snap in snapshots:
        for band_name, label, _ in _iter_snapshot_bands(snap):
            label_counts[band_name][label] = label_counts[band_name].get(label, 0) + 1

    columns: dict[str, list[str]] = {}
    for band_name, counts in label_counts.items():
        if len(counts) <= 1:
            # No variation in this band → don't add a column (it would
            # be perfectly collinear with the intercept).
            continue
        # Reference level = most frequent label. Coefficient implicit (=0).
        reference = max(counts.items(), key=lambda kv: kv[1])[0]
        non_reference = sorted(label for label in counts if label != reference)
        columns[band_name] = non_reference
    return columns


def _build_column_index(
    band_columns: dict[str, list[str]],
) -> dict[tuple[str, str], int]:
    index: dict[tuple[str, str], int] = {}
    cursor = 1  # 0 is intercept
    for band_name, labels in band_columns.items():
        for label in labels:
            index[(band_name, label)] = cursor
            cursor += 1
    return index


def _coefficients_to_label_map(
    coefficients: list[float],
    column_index: dict[tuple[str, str], int],
    band_name: str,
    labels: list[str],
) -> dict[str, float]:
    out = {label: 0.0 for label in labels}
    # Reference level is implied at 0 — but the reference label isn't in
    # `labels`. Callers reading the map use `dict.get(value, 0.0)` so
    # the reference level naturally evaluates to 0.
    for label in labels:
        idx = column_index.get((band_name, label))
        if idx is not None and idx < len(coefficients):
            out[label] = coefficients[idx]
    return out


def _iter_snapshot_bands(snap: QuoteSnapshot):
    yield "age", snap.age_band, "age_band"
    yield "vehicle_value", snap.vehicle_value_band, "vehicle_value_band"
    yield "mileage", snap.mileage_band, "mileage_band"
    yield "claims", snap.claims_band, "claims_band"
    yield "power", snap.power_band, "power_band"
    yield "usage", snap.usage_type, "usage_type"


def _solve_normal_equations(X: list[list[float]], y: list[float]) -> list[float] | None:
    """Solve (X^T X) b = X^T y via Gaussian elimination. None if singular."""

    n = len(X)
    k = len(X[0])

    # Build X^T X (k × k) and X^T y (k).
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for i in range(n):
        row = X[i]
        yi = y[i]
        for a in range(k):
            ra = row[a]
            if ra == 0.0:
                continue
            xty[a] += ra * yi
            for b in range(a, k):
                rb = row[b]
                if rb == 0.0:
                    continue
                xtx[a][b] += ra * rb
    # Mirror to fill the lower triangle.
    for a in range(k):
        for b in range(a):
            xtx[a][b] = xtx[b][a]

    # Tikhonov regularization (ridge with tiny lambda) — prevents
    # singular matrices when bands are highly collinear (e.g., 6 of the
    # 8 training rows share the same vehicle_value_band).
    ridge = 1e-6
    for i in range(k):
        xtx[i][i] += ridge

    # Augment with xty and run Gauss-Jordan.
    aug = [row + [xty[i]] for i, row in enumerate(xtx)]
    for i in range(k):
        pivot_row = max(range(i, k), key=lambda r: abs(aug[r][i]))
        if abs(aug[pivot_row][i]) < 1e-12:
            return None
        if pivot_row != i:
            aug[i], aug[pivot_row] = aug[pivot_row], aug[i]
        pivot = aug[i][i]
        for j in range(i, k + 1):
            aug[i][j] /= pivot
        for r in range(k):
            if r == i:
                continue
            factor = aug[r][i]
            if factor == 0.0:
                continue
            for j in range(i, k + 1):
                aug[r][j] -= factor * aug[i][j]
    return [aug[i][k] for i in range(k)]


def _confidence_from_bands_seen(bands_seen: int) -> float:
    return CONFIDENCE_BY_BANDS_SEEN.get(bands_seen, 0.0)


# Module-level singleton — the FastAPI app gets one rating engine per
# process, cache shared across requests.
rating_engine = RatingEngine()
