"""
FPL Oracle AI - application entry point.

Run with:
    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.config import settings
from app.core.exceptions import FplOracleError
from app.core.logging import configure_logging
from app.db.session import init_db
from app.api.v1.endpoints import fpl, predictions, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks. Scheduler wiring lands here in a later phase."""
    configure_logging()
    logger.info(f"Starting {settings.app_name} [{settings.app_env}]")
    init_db()
    yield
    logger.info("Shutting down FPL Oracle AI")


app = FastAPI(
    title=settings.app_name,
    description="An AI-powered season-long Fantasy Premier League management assistant.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(fpl.router, prefix="/api/v1/fpl", tags=["fpl-integration"])
app.include_router(sync.router, prefix="/api/v1/sync", tags=["data-ingestion"])
app.include_router(
    predictions.router, prefix="/api/v1/predictions", tags=["prediction-engine"]
)


@app.exception_handler(FplOracleError)
async def fpl_oracle_error_handler(request, exc: FplOracleError):
    """Translate domain errors into a consistent JSON error shape."""
    logger.error(f"Domain error on {request.url.path}: {exc}")
    return JSONResponse(status_code=400, content={"error": type(exc).__name__, "detail": str(exc)})


@app.get("/", tags=["health"])
async def root() -> dict:
    """Basic root endpoint."""
    return {"app": settings.app_name, "status": "ok"}


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Health check endpoint used for monitoring / smoke tests."""
    return {
        "status": "healthy",
        "env": settings.app_env,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
    }
