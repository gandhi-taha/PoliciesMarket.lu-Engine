from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk
from app.services.embedding import DeterministicEmbeddingProvider

_embedding_provider = DeterministicEmbeddingProvider()


async def enqueue_document_index(document_id: UUID) -> None:
    """Background-job stub for document indexing."""
    return None


async def refresh_knowledge_corpus() -> None:
    """Background-job stub for corpus refresh."""
    return None


def build_search_vector(text: str) -> str:
    """Placeholder helper for worker-side indexing orchestration."""
    return text


def index_document(session: Session, document: Document) -> None:
    for chunk in document.chunks:
        chunk.embedding = _embedding_provider.embed(chunk.chunk_text)
    session.add(document)
    session.commit()
