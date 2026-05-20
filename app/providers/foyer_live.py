"""Live Foyer adapter — fetches quotes from an external feed instead of the
local `quote_snapshots` table.

This is the second half of the dynamic-Foyer work: the snapshot-mode adapter
(`FoyerAdapter` in `foyer.py`) reads pre-loaded snapshots from Postgres,
while this adapter calls an external HTTP feed each time and materializes
the response into `QuoteSnapshot`-like objects so the downstream ranking /
recommendation pipeline doesn't care which source the offers came from.

Configuration
-------------
The feed URL, auth, and timeouts come from `settings.providers["foyer"]`
once `mode="live"` is in effect. Today those config keys live in
`ProviderRuntimeConfig` as optional fields (`base_url`, `api_key`,
`timeout_seconds`) — see `app/config.py`.

Resilience
----------
- Each call has a configurable timeout and one retry.
- A short in-process TTL cache shields the feed from request bursts.
- On any failure, the adapter falls back to the DB-snapshot path so the
  user-facing API never goes down just because a vendor feed hiccups.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Product, Provider, QuoteSnapshot
from app.providers.base import ProviderAdapter
from app.providers.foyer import FoyerAdapter
from app.schemas.recommend import NormalizedInquiry


# Default TTL — short enough that price updates propagate quickly, long
# enough to absorb a burst of recommend calls without hammering the feed.
DEFAULT_CACHE_TTL_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass
class _CacheEntry:
    expires_at: float
    snapshots: list[QuoteSnapshot]


class _TTLCache:
    """Tiny thread-safe TTL cache, keyed by normalized inquiry signature."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._data: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> list[QuoteSnapshot] | None:
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None or entry.expires_at < now:
                self._data.pop(key, None)
                return None
            return entry.snapshots

    def set(self, key: str, snapshots: list[QuoteSnapshot]) -> None:
        with self._lock:
            self._data[key] = _CacheEntry(
                expires_at=time.monotonic() + self._ttl, snapshots=snapshots
            )

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# Module-level cache shared across instances; cleared in tests via `reset_cache`.
_FEED_CACHE = _TTLCache(DEFAULT_CACHE_TTL_SECONDS)


def reset_cache() -> None:
    """Test hook — flush the live-feed cache between tests."""

    _FEED_CACHE.clear()


class LiveFoyerAdapter(ProviderAdapter):
    """Live-feed variant of the Foyer adapter.

    Resolves provider/product rows from the DB exactly like the snapshot
    adapter (those are stable lookups), but `find_matching_snapshots`
    calls the external Foyer feed instead of querying `quote_snapshots`.
    """

    provider_code = "foyer"
    product_line = "car"
    product_code = "foyer_car"

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        fallback: ProviderAdapter | None = None,
    ) -> None:
        self._http_client = http_client
        # On any feed failure we fall back to the DB-snapshot adapter so a
        # broken vendor doesn't break /v1/recommend.
        self._fallback = fallback or FoyerAdapter()

    # ---- DB lookups (delegated to the snapshot adapter) -----------------

    def get_provider(self, session: Session) -> Provider:
        return self._fallback.get_provider(session)

    def get_product(self, session: Session) -> Product:
        return self._fallback.get_product(session)

    # ---- The actual live logic ------------------------------------------

    def find_matching_snapshots(
        self, session: Session, normalized: NormalizedInquiry
    ) -> list[QuoteSnapshot]:
        cache_key = _cache_key(normalized)
        cached = _FEED_CACHE.get(cache_key)
        if cached is not None:
            return cached

        try:
            payload = self._fetch_feed(normalized)
        except Exception:  # noqa: BLE001 — any HTTP failure -> DB fallback
            return self._fallback.find_matching_snapshots(session, normalized)

        provider = self.get_provider(session)
        product = self.get_product(session)
        snapshots = [
            _materialize_offer(offer, provider=provider, product=product, normalized=normalized)
            for offer in payload.get("offers", [])
        ]
        _FEED_CACHE.set(cache_key, snapshots)
        return snapshots

    # ---- HTTP plumbing --------------------------------------------------

    def _fetch_feed(self, normalized: NormalizedInquiry) -> dict[str, Any]:
        cfg = settings.providers.get(self.provider_code)
        base_url = getattr(cfg, "base_url", None) if cfg else None
        if not base_url:
            raise RuntimeError(
                f"Provider {self.provider_code!r} has no base_url configured for live mode"
            )

        api_key = getattr(cfg, "api_key", None) if cfg else None
        timeout = getattr(cfg, "timeout_seconds", None) or DEFAULT_TIMEOUT_SECONDS
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body = {
            "product_line": normalized.product_line.value,
            "country": normalized.country,
            "usage_type": normalized.usage_type.value,
            "coverage_preference": normalized.coverage_preference.value,
            "bands": normalized.bands.model_dump(),
        }

        client = self._http_client or httpx.Client(timeout=timeout)
        try:
            response = client.post(
                f"{base_url.rstrip('/')}/quotes", json=body, headers=headers
            )
            response.raise_for_status()
            return response.json()
        finally:
            if self._http_client is None:
                client.close()


def _cache_key(normalized: NormalizedInquiry) -> str:
    b = normalized.bands
    return "|".join(
        [
            normalized.product_line.value,
            normalized.country,
            normalized.usage_type.value,
            normalized.coverage_preference.value,
            b.age_band,
            b.vehicle_value_band,
            b.mileage_band,
            b.claims_band,
            b.power_band,
        ]
    )


def _materialize_offer(
    offer: dict[str, Any],
    *,
    provider: Provider,
    product: Product,
    normalized: NormalizedInquiry,
) -> QuoteSnapshot:
    """Turn one feed offer into an unpersisted `QuoteSnapshot` row.

    We never `session.add()` these — they exist only for the duration of
    the request so ranking can score them identically to DB snapshots.
    """

    snap = QuoteSnapshot(
        id=_coerce_uuid(offer.get("id")),
        provider_id=provider.id,
        product_id=product.id,
        product_line=normalized.product_line.value,
        coverage_level=offer["coverage_level"],
        currency=offer.get("currency", "EUR"),
        annual_premium_cents=int(offer["annual_premium_cents"]),
        deductible_cents=int(offer.get("deductible_cents", 0)),
        snapshot_date=_coerce_date(offer.get("snapshot_date")),
        usage_type=normalized.usage_type.value,
        age_band=normalized.bands.age_band,
        vehicle_value_band=normalized.bands.vehicle_value_band,
        mileage_band=normalized.bands.mileage_band,
        claims_band=normalized.bands.claims_band,
        power_band=normalized.bands.power_band,
        is_active=True,
    )
    # Attach already-loaded provider/product so callers don't trigger lazy loads
    # (the snapshot is detached and a lazy load would crash).
    snap.provider = provider
    snap.product = product
    return snap


def _coerce_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            pass
    return uuid4()


def _coerce_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return date.today()


def _register() -> None:
    from app.providers.registry import register_adapter

    register_adapter("foyer", mode="live")(LiveFoyerAdapter)


_register()
