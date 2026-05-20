"""Foyer snapshot adapter.

Foyer keeps a dedicated module (rather than being auto-generated from
providers.yaml like LALUX / Bâloise / AXA) because of two things:

1. Its `live`-mode sibling in `foyer_live.py` imports `FoyerAdapter`
   by name to use as the fallback path on HTTP failure.
2. It's the reference implementation the planning doc points at.

Behaviorally identical to `SnapshotProviderAdapter` — just a thin
subclass with the Foyer codes baked in.
"""

from __future__ import annotations

from app.providers.snapshot_adapter import SnapshotProviderAdapter


class FoyerAdapter(SnapshotProviderAdapter):
    provider_code = "foyer"
    product_code = "foyer_car"
    product_line = "car"


def _register() -> None:
    from app.providers.registry import register_adapter

    register_adapter("foyer", mode="snapshot")(FoyerAdapter)


_register()
