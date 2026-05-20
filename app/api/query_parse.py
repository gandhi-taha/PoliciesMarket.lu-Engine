from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.query import QueryParseRequest, QueryParseResponse
from app.services.query_parser import parse_query

router = APIRouter()


@router.post("/query/parse", response_model=QueryParseResponse)
def query_parse(
    request: QueryParseRequest, db: Session = Depends(get_db)
) -> QueryParseResponse:
    return parse_query(db, request)
