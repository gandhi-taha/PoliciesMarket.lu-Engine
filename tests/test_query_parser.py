import json

from app.providers.mistral_client import ChatResponse, ToolCall
from app.schemas.common import CoverageLevel, ProductLine, UsageType
from app.schemas.query import QueryParseRequest
from app.services.query_parser import parse_query


class FakeClient:
    model = "fake"

    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, **kwargs):
        if not self._responses:
            raise RuntimeError("No more responses")
        return self._responses.pop(0)


def test_query_parser_structured_output() -> None:
    tool_args = {
        "structured": {
            "country": "LU",
            "driver_age": 30,
            "years_licensed": 12,
            "vehicle_value_eur": 22000,
            "vehicle_power_kw": 95,
            "vehicle_age_years": 4,
            "annual_mileage_km": 12000,
            "claims_last_5y": 0,
            "usage_type": "private",
            "coverage_preference": "standard",
        }
    }

    tool_call = ToolCall(call_id="call_1", name="normalize_inquiry", arguments=tool_args)
    tool_message = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "normalize_inquiry", "arguments": json.dumps(tool_args)},
            }
        ],
    }

    response_with_tool = ChatResponse(
        content=None,
        tool_calls=[tool_call],
        message=tool_message,
        raw={},
    )

    response_content = {
        "filters": {
            "product_line": "car",
            "country": "LU",
            "usage_type": "private",
            "coverage_preference": "standard",
            "bands": {
                "age_band": "25-34",
                "vehicle_value_band": "15001-30000",
                "mileage_band": "8001-15000",
                "claims_band": "0",
                "power_band": "81-110",
            },
        },
        "missing_fields": [],
    }

    response_final = ChatResponse(
        content=json.dumps(response_content),
        tool_calls=[],
        message={"role": "assistant", "content": json.dumps(response_content)},
        raw={},
    )

    client = FakeClient([response_with_tool, response_final])
    request = QueryParseRequest(free_text="Need coverage for my car.", structured=None)

    result = parse_query(None, request, client=client)

    assert result.filters.product_line == ProductLine.car
    assert result.filters.usage_type == UsageType.private
    assert result.filters.coverage_preference == CoverageLevel.standard
    assert result.filters.bands is not None
    assert result.missing_fields == []


def test_query_parser_degrades_safely_on_failure() -> None:
    class ErrorClient:
        model = "fake"

        def chat(self, **kwargs):
            raise RuntimeError("boom")

    request = QueryParseRequest(
        free_text="Need a policy.",
        structured={"driver_age": 30},
    )

    result = parse_query(None, request, client=ErrorClient())

    assert "vehicle_value_eur" in result.missing_fields
    assert result.filters.bands is None
