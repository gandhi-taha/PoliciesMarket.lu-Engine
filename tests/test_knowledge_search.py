from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from app.services.knowledge_search import KnowledgeSearchService, SearchResultRow


@dataclass
class ChunkStub:
    id: UUID
    created_at: datetime
    chunk_index: int


def test_reciprocal_rank_fusion_prefers_consensus() -> None:
    service = KnowledgeSearchService()
    first = SearchResultRow(
        chunk=ChunkStub(UUID("00000000-0000-0000-0000-000000000101"), datetime.now(timezone.utc), 1),
        keyword_rank=1,
        vector_rank=None,
        rrf_score=0.0,
    )
    second = SearchResultRow(
        chunk=ChunkStub(UUID("00000000-0000-0000-0000-000000000102"), datetime.now(timezone.utc), 2),
        keyword_rank=2,
        vector_rank=None,
        rrf_score=0.0,
    )
    consensus = SearchResultRow(
        chunk=ChunkStub(UUID("00000000-0000-0000-0000-000000000101"), datetime.now(timezone.utc), 1),
        keyword_rank=None,
        vector_rank=1,
        rrf_score=0.0,
    )

    fused = service.reciprocal_rank_fusion([first, second], [consensus])

    assert fused[0].chunk.id == UUID("00000000-0000-0000-0000-000000000101")
    assert fused[0].rrf_score > fused[1].rrf_score
