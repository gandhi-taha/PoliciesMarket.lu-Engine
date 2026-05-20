from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.query import NormalizedFilters

BASIS_PHRASE = "Based on the compared offers only."


class LLMParseOutput(BaseModel):
    filters: NormalizedFilters
    missing_fields: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


def _reject_legal_advice(value: str) -> str:
    if "legal advice" in value.lower():
        raise ValueError("Legal advice language not allowed")
    return value


class ExplanationResponse(BaseModel):
    summary: str
    highlights: list[str] = Field(default_factory=list)
    basis: str

    model_config = ConfigDict(extra="forbid")

    @field_validator("basis")
    @classmethod
    def basis_must_be_grounded(cls, value: str) -> str:
        if BASIS_PHRASE.lower() not in value.lower():
            raise ValueError("Basis statement missing")
        return value

    @field_validator("summary")
    @classmethod
    def summary_is_safe(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Summary cannot be empty")
        return _reject_legal_advice(value)

    @field_validator("highlights")
    @classmethod
    def highlights_are_safe(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item.strip():
                raise ValueError("Highlight cannot be empty")
            _reject_legal_advice(item)
        return value
