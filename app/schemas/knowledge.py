from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeSearchFilters(BaseModel):
    provider_code: str | None = None
    product_code: str | None = None
    document_type: str | None = None
    language: str | None = None

    model_config = ConfigDict(extra="forbid")


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    filters: KnowledgeSearchFilters = Field(default_factory=KnowledgeSearchFilters)

    model_config = ConfigDict(extra="forbid")


class KnowledgeChunkHit(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_title: str
    source_uri: str | None = None
    chunk_index: int
    snippet: str
    keyword_rank: int | None = None
    vector_rank: int | None = None
    rrf_score: float
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[KnowledgeChunkHit]

    model_config = ConfigDict(extra="forbid")


class RagAnswerRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    filters: KnowledgeSearchFilters = Field(default_factory=KnowledgeSearchFilters)

    model_config = ConfigDict(extra="forbid")


class RagCitation(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_title: str
    source_uri: str | None = None
    chunk_index: int
    rrf_score: float

    model_config = ConfigDict(extra="forbid")


class RagAnswerResponse(BaseModel):
    answer: str
    citations: list[RagCitation]
    evidence_sufficient: bool
    fallback_reason: str | None = None
    prompt_version: str

    model_config = ConfigDict(extra="forbid")


class RagLLMOutput(BaseModel):
    answer: str
    citations: list[UUID] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
