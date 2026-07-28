"""Shared pytest fixtures.

Tests use a fresh in-memory SQLite database per test function, completely
independent from whatever `DATABASE_URL` is configured for the running
application. This keeps model/unit tests fast and side-effect free.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base

# Import the models package so every model is registered on Base.metadata.
import app.models  # noqa: F401


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session backed by a throwaway in-memory database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
