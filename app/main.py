import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.exceptions import (
    ApexAdvisorException,
    LLMInvocationError,
    apex_advisor_exception_handler,
    general_exception_handler,
    sqlalchemy_exception_handler,
)
from app.middleware.rate_limit import RateLimitMiddleware

STATIC_ROOT = Path(__file__).resolve().parent / "static"
_REPO_ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Apply Alembic migrations for local SQLite so register/login work without a manual upgrade step."""
    if (
        not os.environ.get("PYTEST_VERSION")
        and settings.DATABASE_URL.startswith("sqlite")
        and not settings.DATABASE_URL.startswith("sqlite:///:memory:")
    ):
        ini = _REPO_ROOT / "alembic.ini"
        if ini.is_file():
            from alembic import command
            from alembic.config import Config

            cfg = Config(str(ini))
            command.upgrade(cfg, "head")
            # Use uvicorn's logger so the line appears with other startup INFO lines.
            logging.getLogger("uvicorn.error").info(
                "Apex Advisor: SQLite schema ready (alembic upgrade head)."
            )
    yield


OPENAPI_TAGS_METADATA = [
    {"name": "system", "description": "Integration map (`ProfileService` ↔ AI modules)."},
    {"name": "authentication", "description": "Register, login, JWT refresh."},
    {"name": "team", "description": "Layer 1 — team members and behavior tags."},
    {"name": "company", "description": "Layer 2 — company intelligence."},
    {"name": "work", "description": "Layer 3 — deliverables, reviews, follow-ups."},
    {"name": "industry", "description": "Layer 4 — industry intelligence."},
    {"name": "prompts", "description": "Prompt engine and saved templates."},
    {"name": "chat", "description": "Unified chatbox and slash commands."},
    {"name": "nudges", "description": "Contextual banners and cards."},
    {"name": "sync", "description": "Offline queue, revision, conflict detection."},
    {"name": "ai", "description": "AI profiling and multi-turn flows."},
]

APP_DESCRIPTION = """
## Apex Advisor — Workplace adaptation API

Four-layer profiles (**Team → Company → Work → Industry**) are loaded through
`ProfileService` and fed into AI modules (classifiers, reviews, prompt engine, chatbox).

- **Auth**: JWT on `/api/v1/auth/*`
- **Capabilities map**: `GET /api/v1/system/capabilities`
- **OpenAPI**: this document when `DEBUG=true` exposes `/api/v1/openapi.json`

Rate limiting applies to `/api/*` (per IP). LLM calls use up to **3 retries** with exponential backoff.
"""


# Create FastAPI application
app = FastAPI(
    title=settings.PROJECT_NAME,
    description=APP_DESCRIPTION.strip(),
    version="1.0.0",
    openapi_tags=OPENAPI_TAGS_METADATA,
    openapi_url=f"{settings.API_V1_STR}/openapi.json" if settings.DEBUG else None,
    lifespan=_lifespan,
)

# Set up CORS
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(RateLimitMiddleware)

# Include API routes
app.include_router(api_router, prefix=settings.API_V1_STR)


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "message": "Request validation failed",
                "type": "RequestValidationError",
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )


# Add exception handlers
app.add_exception_handler(ApexAdvisorException, apex_advisor_exception_handler)
app.add_exception_handler(LLMInvocationError, apex_advisor_exception_handler)
app.add_exception_handler(RequestValidationError, _validation_exception_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)


@app.get("/")
async def root():
    """Root endpoint for health check."""
    return {
        "message": "Apex Advisor API",
        "version": "1.0.0",
        "status": "healthy"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/ui/team")
async def team_archive_ui():
    """Layer 1 team member archive: structured profile, taboo, AI behavior profiling."""
    page = STATIC_ROOT / "team-archive.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="UI bundle not found")
    return FileResponse(page)


@app.get("/ui/company")
async def company_intelligence_ui():
    """Layer 2 company intelligence: four-dimension knowledge base, digest, stakeholders."""
    page = STATIC_ROOT / "company-intelligence.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="UI bundle not found")
    return FileResponse(page)


@app.get("/ui/work")
async def work_profile_ui():
    """Layer 3 work profile: deliverable standards, reviews, meeting follow-ups."""
    page = STATIC_ROOT / "work-profile.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="UI bundle not found")
    return FileResponse(page)


@app.get("/ui/chat")
async def chatbox_ui():
    """Global AI chatbox: profile context, Context_Tags, slash commands, dual-pane CoT."""
    page = STATIC_ROOT / "chat.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="UI bundle not found")
    return FileResponse(page)


@app.get("/ui/nudges")
async def nudges_ui():
    """Contextual nudges: banners and cards from /api/v1/nudges."""
    page = STATIC_ROOT / "nudges.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="UI bundle not found")
    return FileResponse(page)


@app.get("/ui/industry")
async def industry_intelligence_ui():
    """Layer 4 industry intelligence: four dimensions, topics, data cards, reminders."""
    page = STATIC_ROOT / "industry-intelligence.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="UI bundle not found")
    return FileResponse(page)


app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_ROOT)),
    name="static",
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG
    )