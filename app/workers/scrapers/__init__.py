"""Scrapers, auto-registered from `providers.yaml`.

For every entry in providers.yaml we register a generic scraper that
inherits from `BaseQuoteScraper`. The class is dynamically subclassed
with the insurer's `provider_code` / `product_code` / `public_quote_page`
/ `allowed_by_tos` set from the YAML — no per-insurer file needed.

If an insurer needs bespoke Playwright code (multi-step wizard,
iframes, etc.) that can't be expressed via captured selectors, drop a
module under this package that subclasses `BaseQuoteScraper` and
overrides `prefill_form()` / `extract_result()` directly. The factory
respects pre-existing registrations, so a hand-written subclass beats
the generic one.
"""

from __future__ import annotations

from app.providers.loader import load_providers
from app.workers.scrapers.base import BaseQuoteScraper


_REGISTRY: dict[str, type[BaseQuoteScraper]] = {}


def register(scraper_cls: type[BaseQuoteScraper]) -> type[BaseQuoteScraper]:
    """Decorator for hand-written subclasses that supersede the generic factory."""

    if not scraper_cls.provider_code:
        raise ValueError(
            f"{scraper_cls.__name__}.provider_code must be set before registration."
        )
    _REGISTRY[scraper_cls.provider_code] = scraper_cls
    return scraper_cls


def get_scraper(provider_code: str) -> type[BaseQuoteScraper]:
    if provider_code not in _REGISTRY:
        raise KeyError(
            f"No scraper registered for provider {provider_code!r}. "
            f"Known: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[provider_code]


def list_scrapers() -> list[type[BaseQuoteScraper]]:
    return list(_REGISTRY.values())


def _autoregister_from_yaml() -> None:
    """Create a generic scraper class per providers.yaml entry."""

    for entry in load_providers():
        if entry.code in _REGISTRY:
            # Hand-written override already registered — leave it alone.
            continue
        cls = type(
            f"{entry.code.title()}AutoScraper",
            (BaseQuoteScraper,),
            {
                "provider_code": entry.code,
                "product_code": entry.product_code,
                "PUBLIC_QUOTE_PAGE": entry.public_quote_page,
                "ALLOWED_BY_TOS": entry.allowed_by_tos,
                "MIN_DELAY_SECONDS": entry.min_delay_seconds,
                # The base class's `collect()` is abstract; provide a
                # default that defers to the captured selectors so
                # unattended Tier 3 runs work once ALLOWED_BY_TOS is
                # flipped + selectors are in place. Subclassing for an
                # insurer's bespoke flow is still encouraged for
                # production use.
                "collect": _generic_collect,
                "__doc__": (
                    f"Auto-generated scraper for {entry.name}. "
                    f"Override prefill_form/extract_result in a custom "
                    f"subclass if the captured-selectors path isn't "
                    f"sufficient for this insurer's form."
                ),
            },
        )
        _REGISTRY[entry.code] = cls


def _generic_collect(self, profile):  # type: ignore[no-untyped-def]
    """Default Tier 3 implementation — delegates to selectors JSON.

    Used by the unattended quote_collector orchestrator when
    ALLOWED_BY_TOS=True. Tier 2 (assisted) uses prefill_form / extract_result
    directly, not this method.
    """

    raise NotImplementedError(
        f"Generic Tier 3 collect() not yet wired for {self.provider_code!r}. "
        "Use the Tier 2 assisted collector for now, or write a bespoke "
        "subclass overriding collect()."
    )


_autoregister_from_yaml()
