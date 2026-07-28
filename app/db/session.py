"""Database engine and session management.

This module is the single place that knows how to talk to the configured
database. Because `database_url` is the only thing that changes between
SQLite (development) and PostgreSQL (production), nothing else in the
codebase needs to change to move between them.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from loguru import logger
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _build_engine() -> Engine:
    """Create the SQLAlchemy engine, applying SQLite-specific tweaks."""
    connect_args: dict[str, object] = {}

    if settings.is_sqlite:
        # Needed because FastAPI/APScheduler may use the connection across
        # threads; SQLite forbids this by default.
        connect_args["check_same_thread"] = False
        # settings.ensure_directories() (called on settings init) already
        # creates ./data, so no extra directory handling is needed here.

    engine = create_engine(
        settings.database_url,
        echo=settings.debug,
        connect_args=connect_args,
        future=True,
    )
    logger.debug(f"Database engine created for {settings.database_url}")
    return engine


engine: Engine = _build_engine()

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def init_db() -> None:
    """Create all tables that do not yet exist.

    In development (SQLite) this is sufficient. For production, prefer a
    real migration tool (e.g. Alembic) so schema changes are versioned.
    """
    # Import models so they are registered on Base.metadata before create_all.
    import app.models  # noqa: F401
    from app.db.base import Base

    Base.metadata.create_all(bind=engine)
    logger.info(f"Database schema ensured at {settings.database_url}")


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and closes it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager for use in scripts / services outside of FastAPI.

    Commits on success, rolls back on exception, and always closes the
    session.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()