"""Tests for the live Foyer adapter.

We mock the HTTP layer with `httpx.MockTransport` so no network call is
made. The DB-side methods (`get_provider` / `get_product`) are exercised
through a stub fallback adapter rather than a real session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import uuid4

import httpx

from app.providers import foyer_live
from app.providers.base import ProviderAdapter
from app.providers.foyer_live import LiveFoyerAdapter
from app.schemas.common import CoverageLevel, ProductLine, UsageType
from app.schemas.recommend import InquiryBands, NormalizedInquiry


@dataclass
class _FakeProvider:
    id: object = uuid4()
    code: str = "foyer"


@dataclass
class _FakeProduct:
    id: object = uuid4()
    code: str = "foyer_car"


class _StubFallback(ProviderAdapter):
    """Stub adapter used as the fallback path in LiveFoyerAdapter."""

    provider_code = "foyer"
    product_line = "car"

    def __init__(self) -> None:
        self.provider = _FakeProvider()
        self.product = _FakeProduct()
        self.fallback_calls = 0

    def get_provider(self, session):  # type: ignore[override]
        return self.provider

    def get_product(self, session):  # type: ignore[override]
        return self.product

    def find_matching_snapshots(self, session, normalized):  # type: ignore[override]
        self.fallback_calls += 1
        return ["FALLBACK_USED"]  # sentinel; we don't care about shape here


def _normalized() -> NormalizedInquiry:
    return NormalizedInquiry(
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


def _client_returning(payload: dict, *, capture: list | None = None) -> httpx.Client:
    """Build an httpx.Client whose only response is `payload`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


def test_live_adapter_materializes_offers(monkeypatch) -> None:
    foyer_live.reset_cache()

    # Settings need a base_url for the live adapter to even try the call.
    from app.config import settings

    monkeypatch.setattr(
        settings.providers["foyer"], "base_url", "http://test", raising=False
    )

    client = _client_returning(
        {
            "offers": [
                {
                    "id": str(uuid4()),
                    "coverage_level": "standard",
                    "annual_premium_cents": 71000,
                    "deductible_cents": 40000,
                    "snapshot_date": "2026-05-01",
                    "currency": "EUR",
                }
            ]
        }
    )
    adapter = LiveFoyerAdapter(http_client=client, fallback=_StubFallback())
    snapshots = adapter.find_matching_snapshots(session=None, normalized=_normalized())  # type: ignore[arg-type]

    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.coverage_level == "standard"
    assert snap.annual_premium_cents == 71000
    assert snap.usage_type == "private"
    assert snap.snapshot_date == date(2026, 5, 1)


def test_live_adapter_falls_back_on_http_error(monkeypatch) -> None:
    foyer_live.reset_cache()
    from app.config import settings

    monkeypatch.setattr(
        settings.providers["foyer"], "base_url", "http://test", raising=False
    )

    def boom(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(boom), base_url="http://test")
    fallback = _StubFallback()
    adapter = LiveFoyerAdapter(http_client=client, fallback=fallback)
    snapshots = adapter.find_matching_snapshots(session=None, normalized=_normalized())  # type: ignore[arg-type]

    assert fallback.fallback_calls == 1
    assert snapshots == ["FALLBACK_USED"]


def test_live_adapter_caches_results(monkeypatch) -> None:
    foyer_live.reset_cache()
    from app.config import settings

    monkeypatch.setattr(
        settings.providers["foyer"], "base_url", "http://test", raising=False
    )

    captured: list[httpx.Request] = []
    payload = {
        "offers": [
            {
                "id": str(uuid4()),
                "coverage_level": "basic",
                "annual_premium_cents": 55000,
                "deductible_cents": 60000,
                "snapshot_date": "2026-05-01",
                "currency": "EUR",
            }
        ]
    }
    client = _client_returning(payload, capture=captured)
    adapter = LiveFoyerAdapter(http_client=client, fallback=_StubFallback())

    norm = _normalized()
    adapter.find_matching_snapshots(session=None, normalized=norm)  # type: ignore[arg-type]
    adapter.find_matching_snapshots(session=None, normalized=norm)  # type: ignore[arg-type]

    assert len(captured) == 1, "second call should hit the cache, not the feed"


def test_live_adapter_missing_base_url_falls_back(monkeypatch) -> None:
    foyer_live.reset_cache()
    from app.config import settings

    monkeypatch.setattr(
        settings.providers["foyer"], "base_url", None, raising=False
    )

    fallback = _StubFallback()
    adapter = LiveFoyerAdapter(fallback=fallback)
    snapshots = adapter.find_matching_snapshots(session=None, normalized=_normalized())  # type: ignore[arg-type]
    assert fallback.fallback_calls == 1
    assert snapshots == ["FALLBACK_USED"]
