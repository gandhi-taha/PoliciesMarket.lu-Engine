from __future__ import annotations

from uuid import UUID

from app.services.indexing import enqueue_document_index, refresh_knowledge_corpus


async def run_document_index(document_id: UUID) -> None:
    await enqueue_document_index(document_id)


async def run_refresh() -> None:
    await refresh_knowledge_corpus()
