from app.schemas.recommend import CarInsuranceInquiry
from app.services.normalization import normalize_car_inquiry


def test_normalization_bands() -> None:
    inquiry = CarInsuranceInquiry(
        country="LU",
        driver_age=30,
        years_licensed=12,
        vehicle_value_eur=22000,
        vehicle_power_kw=95,
        vehicle_age_years=4,
        annual_mileage_km=12000,
        claims_last_5y=0,
        usage_type="private",
        coverage_preference="standard",
    )

    normalized = normalize_car_inquiry(inquiry)

    assert normalized.bands.age_band == "25-34"
    assert normalized.bands.vehicle_value_band == "15001-30000"
    assert normalized.bands.mileage_band == "8001-15000"
    assert normalized.bands.claims_band == "0"
    assert normalized.bands.power_band == "81-110"
