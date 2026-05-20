from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class CacheBackend(Protocol):
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...


@dataclass
class NoopCache:
    store: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str) -> Any | None:
        return self.store.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self.store[key] = value
