"""Single source of truth for the engine's provider roster.

Reads `providers.yaml` at the engine repo root and exposes a typed
view. Three things in the codebase read this:

- `app.config` builds `settings.providers` from it
- `app.db.seed` seeds Provider + Product + ProviderConfig rows
- `app.workers.scrapers` registers a generic scraper per entry

Add an insurer = add an entry to `providers.yaml`. No code changes.

The YAML format itself is documented at the top of the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


PROVIDERS_YAML_PATH = Path(__file__).resolve().parents[2] / "providers.yaml"


@dataclass(frozen=True)
class ProviderEntry:
    code: str
    name: str
    enabled: bool
    product_code: str
    product_name: str
    product_line: str
    public_quote_page: str
    min_delay_seconds: float
    allowed_by_tos: bool


@lru_cache(maxsize=1)
def load_providers(path: Path | None = None) -> list[ProviderEntry]:
    """Read providers.yaml and return a list of ProviderEntry.

    Cached at process level — providers don't change at runtime. Tests
    can clear the cache via `load_providers.cache_clear()`.

    If the file is missing, returns an empty list rather than raising.
    That's deliberate: a misconfigured deploy should still let the engine
    boot (the startup banner in `app.main` will surface the empty state).
    """

    target = path or PROVIDERS_YAML_PATH
    if not target.exists():
        return []

    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    providers_raw = raw.get("providers", [])
    if not isinstance(providers_raw, list):
        return []

    out: list[ProviderEntry] = []
    for entry in providers_raw:
        if not isinstance(entry, dict) or "code" not in entry:
            continue
        out.append(
            ProviderEntry(
                code=str(entry["code"]),
                name=str(entry.get("name", entry["code"])),
                enabled=bool(entry.get("enabled", True)),
                product_code=str(
                    entry.get("product_code", f"{entry['code']}_car")
                ),
                product_name=str(
                    entry.get(
                        "product_name", f"{entry.get('name', entry['code'])} Car"
                    )
                ),
                product_line=str(entry.get("product_line", "car")),
                public_quote_page=str(entry.get("public_quote_page", "")),
                min_delay_seconds=float(entry.get("min_delay_seconds", 8.0)),
                allowed_by_tos=bool(entry.get("allowed_by_tos", False)),
            )
        )
    return out


def enabled_providers() -> list[ProviderEntry]:
    return [p for p in load_providers() if p.enabled]


def get_provider(code: str) -> ProviderEntry | None:
    for entry in load_providers():
        if entry.code == code:
            return entry
    return None
