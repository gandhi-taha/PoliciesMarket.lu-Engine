from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Protocol

from app.config import settings


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, text: str) -> list[float]: ...


@dataclass
class DeterministicEmbeddingProvider:
    dimension: int = settings.search.embedding_dimension

    def embed(self, text: str) -> list[float]:
        values = [0.0] * self.dimension
        tokens = [token for token in _tokenize(text) if token]
        if not tokens:
            return values

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for offset in range(0, len(digest), 4):
                index = int.from_bytes(digest[offset : offset + 4], "big") % self.dimension
                values[index] += 1.0

        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            return values
        return [value / norm for value in values]


def _tokenize(text: str) -> list[str]:
    cleaned = text.lower()
    for char in ",.;:!?()[]{}\n\t\r\"'":
        cleaned = cleaned.replace(char, " ")
    return cleaned.split()
