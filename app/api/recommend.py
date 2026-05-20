from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.providers.registry import ProviderNotFoundError, get_adapter
from app.schemas.recommend import CarInsuranceInquiry, RecommendationResponse
from app.services.recommendation_service import (
    recommend_for_inquiry,
    recommend_for_inquiry_multi,
)

router = APIRouter()


@router.post("/recommend", response_model=RecommendationResponse)
def recommend(
    inquiry: CarInsuranceInquiry,
    db: Session = Depends(get_db),
    provider: str | None = Query(
        default=None,
        description=(
            "Optional provider code (e.g. 'foyer'). When supplied, only "
            "that insurer's offers are returned. When omitted (the default), "
            "the engine fans out across every enabled provider in "
            "settings.providers and ranks the merged offers globally."
        ),
    ),
) -> RecommendationResponse:
    if provider is None:
        try:
            return recommend_for_inquiry_multi(db, inquiry)
        except LookupError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        adapter = get_adapter(provider)
    except ProviderNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return recommend_for_inquiry(db, inquiry, adapter)
