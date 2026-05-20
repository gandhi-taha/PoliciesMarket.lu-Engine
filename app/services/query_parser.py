from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.providers.base import ProviderAdapter
from app.providers.mistral_client import ChatResponse, MistralClient, MistralClientProtocol
from app.providers.registry import get_adapter
from app.schemas.common import CoverageLevel, ProductLine
from app.schemas.llm import LLMParseOutput
from app.schemas.query import (
    NormalizedFilters,
    PartialCarInsuranceInquiry,
    QueryParseRequest,
    QueryParseResponse,
)
from app.schemas.recommend import CarInsuranceInquiry, NormalizedInquiry
from app.services.audit import record_audit
from app.services.normalization import normalize_car_inquiry

PROMPT_VERSION = "query-parser.v1"
PROMPT_NAME = "query_parser"
TOOL_NORMALIZE = "normalize_inquiry"
TOOL_SEARCH = "search_quotes"

REQUIRED_FIELDS = [
    "driver_age",
    "years_licensed",
    "vehicle_value_eur",
    "vehicle_power_kw",
    "vehicle_age_years",
    "annual_mileage_km",
    "claims_last_5y",
    "usage_type",
    "coverage_preference",
]


class NormalizeInquiryArgs(BaseModel):
    free_text: str | None = None
    structured: PartialCarInsuranceInquiry | None = None

    model_config = ConfigDict(extra="forbid")


class SearchQuotesArgs(BaseModel):
    filters: NormalizedFilters

    model_config = ConfigDict(extra="forbid")


def parse_query(
    session: Session | None,
    request: QueryParseRequest,
    *,
    client: MistralClientProtocol | None = None,
    adapter: ProviderAdapter | None = None,
) -> QueryParseResponse:
    active_client = client
    # No code passed -> default provider from settings (Foyer by default).
    adapter = adapter or get_adapter()

    input_payload = {
        "free_text": request.free_text,
        "structured": request.structured.model_dump(exclude_none=True, mode="json")
        if request.structured
        else None,
    }

    try:
        if active_client is None:
            active_client = MistralClient.from_settings()
        output = _call_llm(session, request, active_client, adapter)
        record_audit(
            session,
            provider="mistral",
            model=active_client.model,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
            input_payload=input_payload,
            output_payload=output.model_dump(mode="json"),
            status="success",
            error_message=None,
        )
        return QueryParseResponse(
            filters=output.filters,
            missing_fields=output.missing_fields,
            prompt_version=PROMPT_VERSION,
        )
    except Exception as exc:  # noqa: BLE001
        fallback = _fallback_parse(request)
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
        return QueryParseResponse(
            filters=fallback.filters,
            missing_fields=fallback.missing_fields,
            prompt_version=PROMPT_VERSION,
        )


def _call_llm(
    session: Session | None,
    request: QueryParseRequest,
    client: MistralClientProtocol,
    adapter: ProviderAdapter,
) -> LLMParseOutput:
    messages = [
        {
            "role": "system",
            "content": (
                "You parse Luxembourg car insurance requests into structured filters. "
                "Use the normalize_inquiry tool to return structured fields. "
                "Do not invent values, prices, or rankings. "
                "Only call search_quotes if the user explicitly asks to look up offers. "
                "Return JSON only."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "free_text": request.free_text,
                    "structured": request.structured.model_dump(exclude_none=True, mode="json")
                    if request.structured
                    else None,
                }
            ),
        },
    ]

    tools = [_normalize_tool_spec(), _search_tool_spec()]

    for _ in range(2):
        response = client.chat(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=False,
            temperature=0.0,
        )
        if response.tool_calls:
            tool_output = _handle_tool_call(session, adapter, response)
            messages.append(_assistant_message(response))
            messages.append(_tool_message(response.tool_calls[0], tool_output))
            continue
        if response.content:
            return _parse_llm_output(response.content)
        raise ValueError("LLM response missing content")

    raise ValueError("LLM did not return a final response")


def _assistant_message(response: ChatResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.message.get("content"),
        "tool_calls": response.message.get("tool_calls"),
    }


def _tool_message(tool_call, tool_output: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "tool",
        "name": tool_call.name,
        "content": json.dumps(tool_output),
    }
    if tool_call.call_id:
        message["tool_call_id"] = tool_call.call_id
    return message


def _parse_llm_output(content: str) -> LLMParseOutput:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:  # noqa: PERF203
        raise ValueError("LLM returned invalid JSON") from exc
    return LLMParseOutput.model_validate(payload)


def _handle_tool_call(
    session: Session | None,
    adapter: ProviderAdapter,
    response: ChatResponse,
) -> dict[str, Any]:
    if len(response.tool_calls) != 1:
        raise ValueError("Multiple tool calls are not allowed")

    tool_call = response.tool_calls[0]
    if tool_call.name == TOOL_NORMALIZE:
        args = NormalizeInquiryArgs.model_validate(tool_call.arguments)
        output = _normalize_from_partial(args.structured)
        return output.model_dump(mode="json")
    if tool_call.name == TOOL_SEARCH:
        args = SearchQuotesArgs.model_validate(tool_call.arguments)
        return _search_quotes(session, adapter, args.filters)

    raise ValueError(f"Unsupported tool call: {tool_call.name}")


def _normalize_from_partial(
    structured: PartialCarInsuranceInquiry | None,
) -> LLMParseOutput:
    structured = structured or PartialCarInsuranceInquiry()

    missing_fields = [
        field for field in REQUIRED_FIELDS if getattr(structured, field) is None
    ]

    filters = NormalizedFilters(
        product_line=ProductLine.car,
        country=structured.country or "LU",
        usage_type=structured.usage_type,
        coverage_preference=structured.coverage_preference,
        bands=None,
    )

    if missing_fields:
        return LLMParseOutput(filters=filters, missing_fields=missing_fields)

    inquiry = CarInsuranceInquiry.model_validate(
        {
            "country": filters.country,
            "driver_age": structured.driver_age,
            "years_licensed": structured.years_licensed,
            "vehicle_value_eur": structured.vehicle_value_eur,
            "vehicle_power_kw": structured.vehicle_power_kw,
            "vehicle_age_years": structured.vehicle_age_years,
            "annual_mileage_km": structured.annual_mileage_km,
            "claims_last_5y": structured.claims_last_5y,
            "usage_type": structured.usage_type,
            "coverage_preference": structured.coverage_preference,
        }
    )

    normalized = normalize_car_inquiry(inquiry)
    filters = NormalizedFilters(
        product_line=normalized.product_line,
        country=normalized.country,
        usage_type=normalized.usage_type,
        coverage_preference=normalized.coverage_preference,
        bands=normalized.bands,
    )
    return LLMParseOutput(filters=filters, missing_fields=[])


def _search_quotes(
    session: Session | None,
    adapter: ProviderAdapter,
    filters: NormalizedFilters,
) -> dict[str, Any]:
    missing_fields = []
    if filters.bands is None:
        missing_fields.append("bands")
    if filters.usage_type is None:
        missing_fields.append("usage_type")

    if missing_fields:
        return {
            "filters": filters.model_dump(mode="json"),
            "missing_fields": missing_fields,
            "offers": [],
            "count": 0,
        }

    if session is None:
        return {
            "filters": filters.model_dump(mode="json"),
            "missing_fields": ["database_unavailable"],
            "offers": [],
            "count": 0,
        }

    normalized = NormalizedInquiry(
        product_line=filters.product_line or ProductLine.car,
        country=filters.country or "LU",
        usage_type=filters.usage_type,
        coverage_preference=filters.coverage_preference or CoverageLevel.standard,
        bands=filters.bands,
    )

    snapshots = adapter.find_matching_snapshots(session, normalized)
    offers = [_snapshot_to_offer(snapshot) for snapshot in snapshots]

    return {
        "filters": filters.model_dump(mode="json"),
        "missing_fields": [],
        "offers": offers,
        "count": len(offers),
    }


def _snapshot_to_offer(snapshot) -> dict[str, Any]:
    return {
        "provider_code": snapshot.provider.code,
        "product_code": snapshot.product.code,
        "coverage_level": snapshot.coverage_level,
        "annual_premium_cents": snapshot.annual_premium_cents,
        "deductible_cents": snapshot.deductible_cents,
        "currency": snapshot.currency,
    }


def _fallback_parse(request: QueryParseRequest) -> LLMParseOutput:
    structured = request.structured or PartialCarInsuranceInquiry()
    return _normalize_from_partial(structured)


def _normalize_tool_spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NORMALIZE,
            "description": "Normalize a car insurance inquiry into structured filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "free_text": {"type": "string"},
                    "structured": {
                        "type": "object",
                        "properties": {
                            "country": {"type": "string"},
                            "driver_age": {"type": "integer"},
                            "years_licensed": {"type": "integer"},
                            "vehicle_value_eur": {"type": "integer"},
                            "vehicle_power_kw": {"type": "integer"},
                            "vehicle_age_years": {"type": "integer"},
                            "annual_mileage_km": {"type": "integer"},
                            "claims_last_5y": {"type": "integer"},
                            "usage_type": {
                                "type": "string",
                                "enum": ["private", "commute", "business"],
                            },
                            "coverage_preference": {
                                "type": "string",
                                "enum": ["basic", "standard", "premium"],
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["structured"],
                "additionalProperties": False,
            },
        },
    }


def _search_tool_spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_SEARCH,
            "description": "Search stored quotes using normalized filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {
                        "type": "object",
                        "properties": {
                            "product_line": {"type": "string"},
                            "country": {"type": "string"},
                            "usage_type": {
                                "type": "string",
                                "enum": ["private", "commute", "business"],
                            },
                            "coverage_preference": {
                                "type": "string",
                                "enum": ["basic", "standard", "premium"],
                            },
                            "bands": {
                                "type": "object",
                                "properties": {
                                    "age_band": {"type": "string"},
                                    "vehicle_value_band": {"type": "string"},
                                    "mileage_band": {"type": "string"},
                                    "claims_band": {"type": "string"},
                                    "power_band": {"type": "string"},
                                },
                                "additionalProperties": False,
                            },
                        },
                        "additionalProperties": False,
                    }
                },
                "required": ["filters"],
                "additionalProperties": False,
            },
        },
    }
