"""FastAPI application for The Hallucination Trap."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, ORJSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.app.config import get_settings
from api.app.database import get_session
from api.app.embeddings import embed_query
from api.app.models import Article
from api.app.ranking import prewarm_reranker
from api.app.schemas import AuditRequest
from api.app.seed_data import SeedDataError, find_seed, load_seed_data
from api.app.streaming import audit_event_stream

ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = ROOT / "web" / "dist"
RESULTS_PATH = ROOT / "results" / "eval.json"
SEED_RUNS_PATH = ROOT / "results" / "seed_runs.json"
settings = get_settings()
SessionDependency = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Load local models before the service can report ready."""

    await asyncio.gather(
        asyncio.to_thread(embed_query, "Civil Transactions Law قانون المعاملات المدنية"),
        asyncio.to_thread(prewarm_reranker),
    )
    yield


app = FastAPI(
    title="The Hallucination Trap",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)


@app.get("/health")
def health(session: SessionDependency) -> dict[str, object]:
    try:
        article_count = int(session.scalar(select(func.count()).select_from(Article)) or 0)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database check failed: {exc}") from exc
    return {"status": "ok", "articles": article_count, "corpus_ready": article_count == 1422}


@app.get("/api/config")
def configuration(session: SessionDependency) -> dict[str, object]:
    article_count = int(session.scalar(select(func.count()).select_from(Article)) or 0)
    caught_count = None
    if SEED_RUNS_PATH.exists():
        seed_runs = json.loads(SEED_RUNS_PATH.read_text(encoding="utf-8"))
        caught_count = seed_runs.get("verification", {}).get("runs_with_fabricated_article")
    corpus = {
        "law_name_en": "Federal Decree by Law No. 25 of 2025 Civil Transactions Law",
        "law_name_ar": "مرسوم بقانون اتحادي رقم 25 لسنة 2025 بإصدار قانون المعاملات المدنية",
        "law_number": "25",
        "law_year": 2025,
        "article_count": article_count,
        "source_url": "https://uaelegislation.gov.ae/en/legislations/4011",
    }
    return {
        "name": "The Hallucination Trap",
        "default_mode": settings.cache_mode,
        "cache_mode": settings.cache_mode,
        "live_available": settings.has_live_provider,
        "languages": ["en", "ar"],
        "fabricated_citations_caught": caught_count,
        "corpus": corpus,
        "law": corpus,
    }


@app.get("/api/seeds")
def seeds(lang: str | None = None) -> dict[str, object]:
    try:
        payload = load_seed_data()
    except SeedDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    questions = payload["questions"]
    if lang in {"en", "ar"}:
        questions = [question for question in questions if question["language"] == lang]
    return {
        "questions": questions,
        "seeds": questions,
        "evaluation": payload.get("evaluation", {}),
    }


@app.post("/api/audit-stream")
async def audit_stream(request: AuditRequest) -> StreamingResponse:
    if request.mode == "cached" and find_seed(
        seed_id=request.seed_id, question=request.question
    ) is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Cached mode requires one exact committed seed question. "
                "Select a tested trap or switch to Live."
            ),
        )
    return StreamingResponse(
        audit_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/results")
def evaluation_results() -> dict[str, object]:
    if not RESULTS_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Evaluation results are not built. Run make eval.",
        )
    payload = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Evaluation result file is malformed")
    return payload


@app.get("/api/articles/{law_number}/{law_year}/{article_number}")
def article(
    law_number: str,
    law_year: int,
    article_number: str,
    session: SessionDependency,
) -> dict[str, object]:
    row = session.scalar(
        select(Article).where(
            Article.law_number == law_number,
            Article.law_year == law_year,
            Article.article_number == article_number,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Article does not exist in the loaded corpus")
    return {
        "law_name": row.law_name,
        "law_name_ar": row.law_name_ar,
        "law_number": row.law_number,
        "law_year": row.law_year,
        "article_number": row.article_number,
        "article_text_en": row.article_text_en,
        "article_text_ar": row.article_text_ar,
        "source_url": row.source_url,
        "source_url_ar": row.source_url_ar,
        "retrieved_at": row.retrieved_at.isoformat(),
    }


if (WEB_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str) -> FileResponse:
    index = WEB_DIST / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend build is not present")
    return FileResponse(index)
