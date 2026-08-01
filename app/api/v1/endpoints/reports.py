"""
Phase 9: routes for reading and (manually) delivering the weekly report.

Mirrors the project's usual convention (`predictions.py`, `optimization.py`):
one `session_scope()` transaction per request, domain errors translated
into clean HTTP responses rather than raw tracebacks.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.db.session import session_scope
from app.services.reporting import (
    ConsoleDeliveryChannel,
    DeliveryResult,
    WeeklyReportService,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_report_service = WeeklyReportService()


class DeliveryResultOut(BaseModel):
    channel: str
    delivered: bool
    detail: str

    @classmethod
    def from_result(cls, result: DeliveryResult) -> "DeliveryResultOut":
        return cls(channel=result.channel, delivered=result.delivered, detail=result.detail)


@router.get("/{gameweek}", response_class=PlainTextResponse)
async def read_weekly_report(
    gameweek: int,
    format: str = Query(
        default="markdown", pattern="^(markdown|text)$", description="'markdown' or 'text'."
    ),
) -> str:
    """Return the gameweek's weekly report as markdown or plain text.

    Builds on whatever Phases 4-8 have already produced (squad sync,
    predictions, optimizer) - if squad/predictions aren't ready yet, the
    report itself explains what to run first, rather than 404ing.
    """
    with session_scope() as db:
        report = _report_service.build_report(db, gameweek=gameweek)

    return report.to_markdown() if format == "markdown" else report.to_plain_text()


@router.post("/{gameweek}/send", response_model=DeliveryResultOut)
async def send_weekly_report(gameweek: int) -> DeliveryResultOut:
    """Build and deliver the gameweek's report via the console channel.

    This is the same job the scheduler runs automatically when
    `ENABLE_SCHEDULER=true` - exposed here so it can also be triggered
    manually (e.g. to test delivery, or to re-send after a data
    correction) without waiting for the next cron tick.
    """
    with session_scope() as db:
        report = _report_service.build_report(db, gameweek=gameweek)

    channel = ConsoleDeliveryChannel()
    try:
        result = channel.send(report)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    return DeliveryResultOut.from_result(result)
