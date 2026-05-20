from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.providers.mistral_client import MistralClient, MistralClientProtocol
from app.schemas.knowledge import (
    KnowledgeSearchRequest,
    RagAnswerRequest,
    RagAnswerResponse,
    RagLLMOutput,
    RagCitation,
)
from app.schemas.llm import BASIS_PHRASE
from app.services.audit import record_audit
from app.services.knowledge_search import KnowledgeSearchService

PROMPT_VERSION = "rag-answer.v1"
PROMPT_NAME = "rag_answer"


def answer_with_rag(
    session: Session | None,
    request: RagAnswerRequest,
    *,
    client: MistralClientProtocol | None = None,
    search_service: KnowledgeSearchService | None = None,
) -> RagAnswerResponse:
    active_client = client
    search_service = search_service or KnowledgeSearchService()

    input_payload = request.model_dump(mode="json")
    evidence = []
    if session is not None:
        search_request = KnowledgeSearchRequest(
            query=request.query,
            top_k=request.top_k,
            filters=request.filters,
        )
        evidence = search_service.search(session, search_request).results

    try:
        if not evidence:
            fallback = _insufficient_evidence_response()
            record_audit(
                session,
                provider="mistral",
                model=getattr(active_client, "model", "unconfigured") if active_client else "unconfigured",
                prompt_name=PROMPT_NAME,
                prompt_version=PROMPT_VERSION,
                input_payload=input_payload,
                output_payload=fallback.model_dump(mode="json"),
                status="fallback",
                error_message="no_retrieved_chunks",
            )
            return fallback

        if active_client is None:
            active_client = MistralClient.from_settings()

        response = active_client.chat(
            messages=_build_messages(request.query, evidence),
            tools=None,
            tool_choice=None,
            parallel_tool_calls=False,
            temperature=0.1,
        )
        if not response.content:
            raise ValueError("LLM response missing content")

        parsed = _parse_response(response.content)
        citation_map = {hit.chunk_id: hit for hit in evidence}
        citations = [
            _citation_from_hit(citation_map[citation_id])
            for citation_id in parsed.citations
            if citation_id in citation_map
        ]
        if not citations:
            raise ValueError("No matching citations")
        result = RagAnswerResponse(
            answer=parsed.answer,
            citations=citations,
            evidence_sufficient=True,
            fallback_reason=None,
            prompt_version=PROMPT_VERSION,
        )
        record_audit(
            session,
            provider="mistral",
            model=active_client.model,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
            input_payload=input_payload,
            output_payload=result.model_dump(mode="json"),
            status="success",
            error_message=None,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        fallback = _insufficient_evidence_response(reason=str(exc))
        record_audit(
            session,
            provider="mistral",
            model=getattr(active_client or client, "model", "unconfigured"),
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
            input_payload=input_payload,
            output_payload=fallback.model_dump(mode="json"),
            status="error",
            error_message=str(exc),
        )
        return fallback


def _build_messages(query: str, evidence: list[Any]) -> list[dict[str, Any]]:
    serialized = [
        {
            "chunk_id": str(hit.chunk_id),
            "document_id": str(hit.document_id),
            "document_title": hit.document_title,
            "source_uri": hit.source_uri,
            "chunk_index": hit.chunk_index,
            "snippet": hit.snippet,
        }
        for hit in evidence
    ]
    return [
        {
            "role": "system",
            "content": (
                "Answer only from the retrieved chunks. Do not invent facts or pricing. "
                "If there is not enough evidence, say so plainly. Do not provide legal advice. "
                "Return JSON with keys: answer, citations. The answer must mention that it is based on retrieved chunks only."
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"query": query, "chunks": serialized, "basis": BASIS_PHRASE}),
        },
    ]


def _parse_response(content: str) -> RagLLMOutput:
    payload = json.loads(content)
    parsed = RagLLMOutput.model_validate(payload)
    if not parsed.answer.strip():
        raise ValueError("Missing answer")
    if "retrieved chunks" not in parsed.answer.lower():
        raise ValueError("Answer is not grounded")
    return parsed


def _citation_from_hit(hit) -> RagCitation:
    return RagCitation(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        document_title=hit.document_title,
        source_uri=hit.source_uri,
        chunk_index=hit.chunk_index,
        rrf_score=hit.rrf_score,
    )


def _insufficient_evidence_response(reason: str | None = None) -> RagAnswerResponse:
    return RagAnswerResponse(
        answer="I do not have enough retrieved chunks to answer that safely.",
        citations=[],
        evidence_sufficient=False,
        fallback_reason=reason or "insufficient_evidence",
        prompt_version=PROMPT_VERSION,
    )
