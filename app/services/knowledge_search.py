from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Document, DocumentChunk
from app.schemas.knowledge import KnowledgeChunkHit, KnowledgeSearchFilters, KnowledgeSearchRequest, KnowledgeSearchResponse
from app.services.cache import CacheBackend, NoopCache
from app.services.embedding import DeterministicEmbeddingProvider, EmbeddingProvider
from app.services.observability import NoopObservability, ObservabilityHook

RRF_K = settings.search.rrf_k


@dataclass
class SearchResultRow:
    chunk: DocumentChunk
    keyword_rank: int | None
    vector_rank: int | None
    rrf_score: float


class KnowledgeSearchService:
    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        cache: CacheBackend | None = None,
        observability: ObservabilityHook | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider or DeterministicEmbeddingProvider()
        self.cache = cache or NoopCache()
        self.observability = observability or NoopObservability()

    def search(self, session: Session, request: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
        cache_key = _cache_key(request)
        cached = self.cache.get(cache_key)
        if isinstance(cached, KnowledgeSearchResponse):
            self.observability.track_event("knowledge_search.cache_hit", {"query": request.query})
            return cached

        self.observability.track_event("knowledge_search.start", {"query": request.query})
        keyword_hits = self.keyword_search(session, request)
        vector_hits = self.vector_search(session, request)
        fused = self.reciprocal_rank_fusion(keyword_hits, vector_hits)
        response = KnowledgeSearchResponse(
            query=request.query,
            results=[_to_hit(row) for row in fused[: request.top_k]],
        )
        self.cache.set(cache_key, response, settings.search.cache_ttl_seconds)
        self.observability.track_event(
            "knowledge_search.complete",
            {"query": request.query, "count": len(response.results)},
        )
        return response

    def keyword_search(self, session: Session, request: KnowledgeSearchRequest) -> list[SearchResultRow]:
        query = request.query.strip()
        stmt = (
            select(DocumentChunk)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(self._filters_clause(request.filters))
            .where(
                DocumentChunk.search_vector.op("@@")(
                    func.websearch_to_tsquery("simple", query)
                )
            )
            .options(selectinload(DocumentChunk.document))
            .order_by(
                func.ts_rank_cd(
                    DocumentChunk.search_vector,
                    func.websearch_to_tsquery("simple", query),
                ).desc(),
                DocumentChunk.created_at.desc(),
            )
            .limit(request.top_k * 3)
        )
        rows = session.execute(stmt).scalars().all()
        return [
            SearchResultRow(chunk=row, keyword_rank=index + 1, vector_rank=None, rrf_score=0.0)
            for index, row in enumerate(rows)
        ]

    def vector_search(self, session: Session, request: KnowledgeSearchRequest) -> list[SearchResultRow]:
        embedding = self.embedding_provider.embed(request.query)
        if not embedding:
            return []

        stmt = (
            select(DocumentChunk)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(self._filters_clause(request.filters))
            .where(DocumentChunk.embedding.is_not(None))
            .options(selectinload(DocumentChunk.document))
            .order_by(DocumentChunk.embedding.cosine_distance(embedding))
            .limit(request.top_k * 3)
        )
        rows = session.execute(stmt).scalars().all()
        return [
            SearchResultRow(chunk=row, keyword_rank=None, vector_rank=index + 1, rrf_score=0.0)
            for index, row in enumerate(rows)
        ]

    def reciprocal_rank_fusion(
        self,
        keyword_rows: list[SearchResultRow],
        vector_rows: list[SearchResultRow],
    ) -> list[SearchResultRow]:
        fused: dict[UUID, SearchResultRow] = {}
        for row in keyword_rows:
            self._accumulate_rrf(fused, row.chunk.id, row, row.keyword_rank, None)
        for row in vector_rows:
            self._accumulate_rrf(fused, row.chunk.id, row, None, row.vector_rank)

        ordered = sorted(
            fused.values(),
            key=lambda row: (
                -row.rrf_score,
                row.chunk.created_at,
                row.chunk.chunk_index,
            ),
        )
        return ordered

    def _accumulate_rrf(
        self,
        fused: dict[UUID, SearchResultRow],
        chunk_id: UUID,
        row: SearchResultRow,
        keyword_rank: int | None,
        vector_rank: int | None,
    ) -> None:
        current = fused.get(chunk_id)
        score_delta = 0.0
        if keyword_rank is not None:
            score_delta += 1.0 / (RRF_K + keyword_rank)
        if vector_rank is not None:
            score_delta += 1.0 / (RRF_K + vector_rank)

        if current is None:
            fused[chunk_id] = SearchResultRow(
                chunk=row.chunk,
                keyword_rank=keyword_rank,
                vector_rank=vector_rank,
                rrf_score=score_delta,
            )
            return

        fused[chunk_id] = SearchResultRow(
            chunk=current.chunk,
            keyword_rank=current.keyword_rank or keyword_rank,
            vector_rank=current.vector_rank or vector_rank,
            rrf_score=current.rrf_score + score_delta,
        )

    def _filters_clause(self, filters: KnowledgeSearchFilters):
        conditions = [Document.status == "active", DocumentChunk.chunk_text.is_not(None)]
        if filters.provider_code:
            conditions.append(Document.provider.has(code=filters.provider_code))
        if filters.product_code:
            conditions.append(Document.product.has(code=filters.product_code))
        if filters.document_type:
            conditions.append(Document.document_type == filters.document_type)
        if filters.language:
            conditions.append(Document.language == filters.language)
        return and_(*conditions)


def _cache_key(request: KnowledgeSearchRequest) -> str:
    filters = request.filters.model_dump(mode="json")
    return f"knowledge:{request.query}:{request.top_k}:{filters}"


def _to_hit(row: SearchResultRow) -> KnowledgeChunkHit:
    document = row.chunk.document
    return KnowledgeChunkHit(
        chunk_id=row.chunk.id,
        document_id=row.chunk.document_id,
        document_title=document.title,
        source_uri=document.source_uri,
        chunk_index=row.chunk.chunk_index,
        snippet=row.chunk.chunk_text[:400],
        keyword_rank=row.keyword_rank,
        vector_rank=row.vector_rank,
        rrf_score=row.rrf_score,
        created_at=row.chunk.created_at,
    )
