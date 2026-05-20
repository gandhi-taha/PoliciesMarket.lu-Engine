from fastapi import FastAPI

from app.api.admin_providers import router as admin_providers_router
from app.api.health import router as health_router
from app.api.query_parse import router as query_parse_router
from app.api.rag_answer import router as rag_answer_router
from app.api.recommend import router as recommend_router
from app.api.search_knowledge import router as search_knowledge_router
from app.config import settings
from app.startup_check import emit_readiness_banner

app = FastAPI(title=settings.app_name)

app.include_router(recommend_router, prefix=settings.api_prefix, tags=["recommend"])
app.include_router(query_parse_router, prefix=settings.api_prefix, tags=["query"])
app.include_router(search_knowledge_router, prefix=settings.api_prefix, tags=["search"])
app.include_router(rag_answer_router, prefix=settings.api_prefix, tags=["rag"])
app.include_router(health_router, tags=["health"])
# Admin endpoints are deliberately *not* under `/v1` — operator-only surface.
app.include_router(admin_providers_router)


@app.on_event("startup")
def _startup_banner() -> None:
    """Print a data-readiness banner to stderr on every boot.

    Surfaces "no real data has been collected yet" loudly so ops knows
    to run `./bootstrap.sh`. The check itself is fast (one grouped
    count query) so it's safe to run on every cold start, including
    autoscale events.
    """

    try:
        emit_readiness_banner()
    except Exception:  # noqa: BLE001 — never break startup on a banner
        pass
