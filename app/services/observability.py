from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ObservabilityHook(Protocol):
    def track_event(self, name: str, properties: dict[str, Any] | None = None) -> None: ...

    def track_timing(self, name: str, value_ms: float, properties: dict[str, Any] | None = None) -> None: ...


@dataclass
class NoopObservability:
    events: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)

    def track_event(self, name: str, properties: dict[str, Any] | None = None) -> None:
        self.events.append((name, properties))

    def track_timing(self, name: str, value_ms: float, properties: dict[str, Any] | None = None) -> None:
        self.events.append((f"timing:{name}", {"value_ms": value_ms, **(properties or {})}))
