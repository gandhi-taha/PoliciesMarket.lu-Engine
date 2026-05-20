from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.knowledge import KnowledgeSearchRequest, KnowledgeSearchResponse
from app.services.knowledge_search import KnowledgeSearchService

router = APIRouter()


@router.post("/search/knowledge", response_model=KnowledgeSearchResponse)
def search_knowledge(
    request: KnowledgeSearchRequest, db: Session = Depends(get_db)
) -> KnowledgeSearchResponse:
    service = KnowledgeSearchService()
    return service.search(db, request)
