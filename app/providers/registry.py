"""Dynamic provider registry.

Replaces the previous hardcoded `FoyerAdapter()` instantiations sprinkled
across the API and service layers. Adapters are registered by `provider_code`
and selected at request time, either explicitly (caller passes a code) or
by consulting `app.config.settings.providers` for the default and the
adapter `mode` (e.g. `snapshot` vs `live`).

Usage
-----
    from app.providers.registry import get_adapter

    # default provider, default mode (driven by settings)
    adapter = get_adapter()

    # explicit code, e.g. for a multi-provider query
    adapter = get_adapter("foyer")

    # iterate all enabled providers (multi-provider recommend mode)
    for adapter in iter_enabled_adapters():
        ...

Adding a new provider
---------------------
    from app.providers.registry import register_adapter

    @register_adapter("baloise")
    class BaloiseAdapter(ProviderAdapter):
        ...

The decorator can also take `mode="live"` so the same provider code can
have multiple adapter implementations (one snapshot-based, one live-feed).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from app.config import settings
from app.providers.base import ProviderAdapter

if TYPE_CHECKING:  # pragma: no cover
    pass


# Default mode used when settings don't specify one. "snapshot" means the
# adapter reads from the quote_snapshots table; "live" means it pulls from
# an external feed.
DEFAULT_MODE = "snapshot"


# (provider_code, mode) -> factory(zero-arg callable returning a ProviderAdapter)
_FACTORIES: dict[tuple[str, str], Callable[[], ProviderAdapter]] = {}


class ProviderNotFoundError(LookupError):
    """Raised when an adapter cannot be resolved for the given code/mode."""


def register_adapter(
    code: str, *, mode: str = DEFAULT_MODE
) -> Callable[[type[ProviderAdapter] | Callable[[], ProviderAdapter]],
              type[ProviderAdapter] | Callable[[], ProviderAdapter]]:
    """Decorator: register a `ProviderAdapter` subclass (or factory) under (code, mode).

    Re-registering the same key overwrites the previous entry. This is
    deliberate so tests can swap in fakes via the same decorator.
    """

    def decorator(target):
        # Accept either a class (instantiated with no args) or a factory callable.
        factory: Callable[[], ProviderAdapter]
        if isinstance(target, type):
            factory = target  # class is callable -> returns instance
        else:
            factory = target
        _FACTORIES[(code, mode)] = factory
        return target

    return decorator


def get_adapter(code: str | None = None, *, mode: str | None = None) -> ProviderAdapter:
    """Resolve an adapter instance.

    - `code=None` picks the first enabled provider from settings (Foyer by default).
    - `mode=None` reads the mode from `settings.providers[code].mode`, falling
      back to DEFAULT_MODE.
    """

    resolved_code = code or _default_provider_code()
    resolved_mode = mode or _mode_for(resolved_code)

    factory = _FACTORIES.get((resolved_code, resolved_mode))
    if factory is None:
        # Fall back to snapshot mode if a specific mode isn't registered.
        factory = _FACTORIES.get((resolved_code, DEFAULT_MODE))
    if factory is None:
        raise ProviderNotFoundError(
            f"No adapter registered for provider={resolved_code!r}, mode={resolved_mode!r}. "
            f"Known: {sorted(_FACTORIES.keys())}"
        )
    return factory()


def iter_enabled_adapters() -> Iterator[ProviderAdapter]:
    """Yield one configured adapter per enabled provider in `settings.providers`.

    Used by the recommendation flow if you want to fan-out across every
    active provider rather than just the default one.
    """

    for code, cfg in settings.providers.items():
        if not cfg.enabled:
            continue
        try:
            yield get_adapter(code)
        except ProviderNotFoundError:
            # Configured but no adapter implementation yet: skip silently.
            continue


def registered_keys() -> list[tuple[str, str]]:
    """Inspection helper, mostly for tests."""

    return sorted(_FACTORIES.keys())


def _default_provider_code() -> str:
    for code, cfg in settings.providers.items():
        if cfg.enabled:
            return code
    raise ProviderNotFoundError(
        "No enabled providers in settings.providers; cannot pick a default."
    )


def _mode_for(code: str) -> str:
    cfg = settings.providers.get(code)
    if cfg is None:
        return DEFAULT_MODE
    # ProviderRuntimeConfig may carry an optional `mode` attribute; if not,
    # fall back to DEFAULT_MODE.
    return getattr(cfg, "mode", None) or DEFAULT_MODE


def _autoload_builtin_adapters() -> None:
    """Register a snapshot adapter per providers.yaml entry.

    Foyer keeps its dedicated module because of the live-mode sibling
    (`foyer_live.py`). Every other insurer gets a generic snapshot
    adapter created on the fly from the YAML row.
    """

    # Importing for side-effects (these modules call @register_adapter
    # at import time and add `live` mode variants where applicable).
    import app.providers.foyer  # noqa: F401
    import app.providers.foyer_live  # noqa: F401

    # Generic snapshot adapters for everyone else in providers.yaml.
    from app.providers.loader import load_providers
    from app.providers.snapshot_adapter import SnapshotProviderAdapter

    for entry in load_providers():
        if entry.code == "foyer":
            continue
        if (entry.code, DEFAULT_MODE) in _FACTORIES:
            continue

        def _make_factory(code: str, product_code: str, product_line: str):
            def factory() -> ProviderAdapter:
                return SnapshotProviderAdapter(
                    provider_code=code,
                    product_code=product_code,
                    product_line=product_line,
                )

            return factory

        _FACTORIES[(entry.code, DEFAULT_MODE)] = _make_factory(
            entry.code, entry.product_code, entry.product_line
        )


_autoload_builtin_adapters()
