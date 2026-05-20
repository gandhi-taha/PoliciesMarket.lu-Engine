from datetime import date
from uuid import UUID

from app.providers.mistral_client import ChatResponse
from app.schemas.common import CoverageLevel, ProductLine, UsageType
from app.schemas.llm import BASIS_PHRASE
from app.schemas.recommend import QuoteSnapshotOut
from app.services.explainer import generate_explanation


class ErrorClient:
    model = "fake"

    def chat(self, **kwargs):
        raise RuntimeError("boom")


def test_explainer_degrades_safely_on_failure() -> None:
    offer = QuoteSnapshotOut(
        id=UUID("00000000-0000-0000-0000-000000000002"),
        provider_code="foyer",
        product_code="foyer_car",
        product_line=ProductLine.car,
        coverage_level=CoverageLevel.standard,
        currency="EUR",
        annual_premium_cents=70000,
        deductible_cents=40000,
        snapshot_date=date(2026, 5, 1),
        usage_type=UsageType.private,
        age_band="25-34",
        vehicle_value_band="15001-30000",
        mileage_band="8001-15000",
        claims_band="0",
        power_band="81-110",
    )

    result = generate_explanation(None, [offer], client=ErrorClient())

    assert result.basis == BASIS_PHRASE
