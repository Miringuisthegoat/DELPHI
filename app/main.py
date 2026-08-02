"""
FPL Oracle AI - application entry point.

Run with:
    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.core.config import settings
from app.core.exceptions import FplOracleError
from app.core.logging import configure_logging
from app.db.session import init_db
from app.api.v1.endpoints import fpl, optimization, pipeline, predictions, reports, squad, sync
from app.scheduler.jobs import start_scheduler, stop_scheduler
from app.web import routes as dashboard_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks."""
    configure_logging()
    logger.info(f"Starting {settings.app_name} [{settings.app_env}]")
    init_db()
    start_scheduler()
    yield
    stop_scheduler()
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
app.include_router(
    optimization.router, prefix="/api/v1/optimization", tags=["transfer-optimizer"]
)
app.include_router(squad.router, prefix="/api/v1/squad", tags=["squad-management"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["weekly-reporting"])
app.include_router(pipeline.router, prefix="/api/v1/pipeline", tags=["weekly-pipeline"])

# Phase 8: dashboard (HTML, not JSON) + its static assets.
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(dashboard_routes.router, tags=["dashboard"])


@app.exception_handler(FplOracleError)
async def fpl_oracle_error_handler(request, exc: FplOracleError):
    """Translate domain errors into a consistent JSON error shape."""
    logger.error(f"Domain error on {request.url.path}: {exc}")
    return JSONResponse(status_code=400, content={"error": type(exc).__name__, "detail": str(exc)})


@app.get("/", tags=["health"])
async def root() -> dict:
    """Basic root endpoint. See /dashboard for the DELPHI UI."""
    return {"app": settings.app_name, "status": "ok", "dashboard": "/dashboard"}


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Health check endpoint used for monitoring / smoke tests."""
    return {
        "status": "healthy",
        "env": settings.app_env,
        "database": "sqlite" if settings.is_sqlite else "postgresql",
        "scheduler_enabled": settings.enable_scheduler,
    }
