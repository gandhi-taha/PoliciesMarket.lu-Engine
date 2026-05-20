"""Registry behavior: lookup, mode resolution, fallback, listing."""

from __future__ import annotations

import pytest

from app.providers import registry
from app.providers.base import ProviderAdapter
from app.providers.foyer import FoyerAdapter
from app.providers.foyer_live import LiveFoyerAdapter


def test_default_lookup_returns_foyer_snapshot_adapter() -> None:
    adapter = registry.get_adapter()
    assert isinstance(adapter, FoyerAdapter)


def test_explicit_code_returns_same_adapter() -> None:
    adapter = registry.get_adapter("foyer")
    assert isinstance(adapter, FoyerAdapter)


def test_explicit_live_mode_returns_live_adapter() -> None:
    adapter = registry.get_adapter("foyer", mode="live")
    assert isinstance(adapter, LiveFoyerAdapter)


def test_unknown_provider_raises() -> None:
    with pytest.raises(registry.ProviderNotFoundError):
        registry.get_adapter("nonexistent_provider_zzz")


def test_register_then_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAdapter(ProviderAdapter):
        provider_code = "fake"
        product_line = "car"

        def get_provider(self, session): ...  # type: ignore[override]
        def get_product(self, session): ...  # type: ignore[override]
        def find_matching_snapshots(self, session, normalized):  # type: ignore[override]
            return []

    registry.register_adapter("fake_test_only")(FakeAdapter)
    try:
        adapter = registry.get_adapter("fake_test_only")
        assert isinstance(adapter, FakeAdapter)
    finally:
        # Clean up the registry slot so other tests aren't polluted.
        registry._FACTORIES.pop(("fake_test_only", registry.DEFAULT_MODE), None)


def test_registered_keys_include_foyer_pair() -> None:
    keys = registry.registered_keys()
    assert ("foyer", "snapshot") in keys
    assert ("foyer", "live") in keys
