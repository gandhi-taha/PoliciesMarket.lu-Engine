from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.providers.mistral_client import MistralClient, MistralClientProtocol
from app.schemas.llm import BASIS_PHRASE, ExplanationResponse
from app.schemas.recommend import QuoteSnapshotOut
from app.services.audit import record_audit

PROMPT_VERSION = "explainer.v1"
PROMPT_NAME = "explainer"


def generate_explanation(
    session: Session | None,
    offers: list[QuoteSnapshotOut],
    *,
    client: MistralClientProtocol | None = None,
) -> ExplanationResponse:
    active_client = client

    input_payload = {"offers": [_offer_payload(offer, idx) for idx, offer in enumerate(offers)]}

    try:
        if active_client is None:
            active_client = MistralClient.from_settings()
        response = active_client.chat(
            messages=_build_messages(offers),
            tools=None,
            tool_choice=None,
            parallel_tool_calls=False,
            temperature=0.2,
        )
        if not response.content:
            raise ValueError("LLM response missing content")
        explanation = _parse_explanation(response.content)
        record_audit(
            session,
            provider="mistral",
            model=active_client.model,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
            input_payload=input_payload,
            output_payload=explanation.model_dump(mode="json"),
            status="success",
            error_message=None,
        )
        return explanation
    except Exception as exc:  # noqa: BLE001
        fallback = _fallback_explanation(offers)
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


def _build_messages(offers: list[QuoteSnapshotOut]) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": (
                "You explain insurance offers in a concise, grounded way. "
                "Use only the provided offers, do not invent prices or details. "
                "Do not provide legal advice. Do not recommend beyond the compared offers. "
                "Return JSON with keys: summary, highlights, basis. "
                f"The basis must include: '{BASIS_PHRASE}'."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "offers": [_offer_payload(offer, idx) for idx, offer in enumerate(offers)],
                    "notes": "The offers are already ranked."
                }
            ),
        },
    ]


def _offer_payload(offer: QuoteSnapshotOut, index: int) -> dict[str, Any]:
    return {
        "rank": index + 1,
        "provider_code": offer.provider_code,
        "product_code": offer.product_code,
        "coverage_level": offer.coverage_level.value,
        "annual_premium_cents": offer.annual_premium_cents,
        "deductible_cents": offer.deductible_cents,
        "currency": offer.currency,
    }


def _parse_explanation(content: str) -> ExplanationResponse:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:  # noqa: PERF203
        raise ValueError("LLM returned invalid JSON") from exc
    return ExplanationResponse.model_validate(payload)


def _fallback_explanation(offers: list[QuoteSnapshotOut]) -> ExplanationResponse:
    if not offers:
        summary = "No offers are available to compare right now."
        highlights: list[str] = []
    else:
        summary = "Compared the available offers on coverage level and cost structure."
        highlights = [
            "Coverage levels differ across the compared offers.",
            "Premium and deductible levels vary between options.",
        ]

    return ExplanationResponse(
        summary=summary,
        highlights=highlights,
        basis=BASIS_PHRASE,
    )
