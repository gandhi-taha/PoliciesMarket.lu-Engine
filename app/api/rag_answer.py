from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.knowledge import RagAnswerRequest, RagAnswerResponse
from app.services.rag import answer_with_rag

router = APIRouter()


@router.post("/rag/answer", response_model=RagAnswerResponse)
def rag_answer(request: RagAnswerRequest, db: Session = Depends(get_db)) -> RagAnswerResponse:
    return answer_with_rag(db, request)
