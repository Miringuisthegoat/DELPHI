"""Data ingestion package: persists typed FPL API payloads into the database."""

from app.services.ingestion.service import (
    DataIngestionService,
    FullSyncSummary,
    IngestionResult,
)

__all__ = [
    "DataIngestionService",
    "FullSyncSummary",
    "IngestionResult",
]
