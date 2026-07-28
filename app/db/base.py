"""Declarative base and shared mixins for all ORM models."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Return the current UTC time (used as a default for timestamp columns)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Shared declarative base class for every ORM model in the app."""


class TimestampMixin:
    """Adds `created_at` / `updated_at` bookkeeping columns to a model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
