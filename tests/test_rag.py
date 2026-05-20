import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from app.providers.mistral_client import ChatResponse
from app.schemas.knowledge import KnowledgeChunkHit, KnowledgeSearchFilters, RagAnswerRequest
from app.services.rag import answer_with_rag


class FakeClient:
    model = "fake"

    def __init__(self, response):
        self._response = response

    def chat(self, **kwargs):
        return self._response


class FakeSearchService:
    def __init__(self, hits):
        self._hits = hits

    def search(self, session, request):
        return SimpleNamespace(results=self._hits)


class DummySession:
    pass


def test_rag_returns_grounded_answer_with_citations() -> None:
    chunk_id = UUID("00000000-0000-0000-0000-000000000201")
    evidence = [
        KnowledgeChunkHit(
            chunk_id=chunk_id,
            document_id=UUID("00000000-0000-0000-0000-000000000200"),
            document_title="Foyer Car Insurance Guide",
            source_uri="seed://foyer/car/guide",
            chunk_index=1,
            snippet="Foyer car insurance includes basic, standard, and premium coverage tiers.",
            keyword_rank=1,
            vector_rank=1,
            rrf_score=0.03,
            created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
    ]
    payload = {
        "answer": "Based on retrieved chunks only, the offers differ by coverage level.",
        "citations": [str(chunk_id)],
    }
    response = ChatResponse(
        content=json.dumps(payload),
        tool_calls=[],
        message={"role": "assistant", "content": json.dumps(payload)},
        raw={},
    )

    result = answer_with_rag(
        DummySession(),
        RagAnswerRequest(query="What options exist?", top_k=5, filters=KnowledgeSearchFilters()),
        client=FakeClient(response),
        search_service=FakeSearchService(evidence),
    )

    assert result.evidence_sufficient is True
    assert result.citations[0].chunk_id == chunk_id
    assert "retrieved chunks" in result.answer.lower()


def test_rag_falls_back_when_no_evidence() -> None:
    result = answer_with_rag(
        DummySession(),
        RagAnswerRequest(query="What options exist?", top_k=5, filters=KnowledgeSearchFilters()),
        client=FakeClient(ChatResponse(content=None, tool_calls=[], message={}, raw={})),
        search_service=FakeSearchService([]),
    )

    assert result.evidence_sufficient is False
    assert result.citations == []
