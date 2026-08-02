"""
Phase 10: an opt-in, heavier scheduled job that runs predict -> evaluate
-> report as one unit, instead of Phase 9's report-only job.

This is deliberately NOT wired into `start_scheduler()` by default.
`app.scheduler.jobs.start_scheduler()` keeps registering the Phase 9
`generate_and_deliver_weekly_report` job (report-only, reads whatever
predictions already exist) so enabling `ENABLE_SCHEDULER=true` doesn't
change behaviour for anyone already relying on it.

To use this instead, swap the job registered in `app/scheduler/jobs.py`:

    from app.scheduler.pipeline_jobs import generate_predict_and_report

    scheduler.add_job(
        generate_predict_and_report,
        trigger=CronTrigger.from_crontab(settings.weekly_update_cron),
        id="delphi_weekly_report",
        replace_existing=True,
    )

Still does not sync live FPL data or squad picks - see
`WeeklyPipelineService`'s docstring for why that stays a separate,
manually-triggered (or separately-scheduled) step.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select

from app.db.session import session_scope
from app.models.squad import SquadState
from app.services.pipeline import WeeklyPipelineService
from app.services.reporting import ConsoleDeliveryChannel, DeliveryChannel


def _delivery_channel() -> DeliveryChannel:
    """Same seam as `app.scheduler.jobs._delivery_channel` - swap once a
    real Telegram/Discord channel is implemented."""
    return ConsoleDeliveryChannel()


def _resolve_current_gameweek(db) -> int | None:
    return (
        db.execute(select(SquadState.gameweek).order_by(SquadState.gameweek.desc()))
        .scalars()
        .first()
    )


def generate_predict_and_report() -> None:
    """Full local pipeline as a scheduled job body: predict, evaluate, report.

    Safe to call manually (script, API route, or a scheduler job) - no
    dependency on being invoked by APScheduler specifically.
    """
    pipeline = WeeklyPipelineService()
    channel = _delivery_channel()

    with session_scope() as db:
        gameweek = _resolve_current_gameweek(db)
        if gameweek is None:
            logger.warning(
                "Scheduled weekly pipeline skipped: no squad state synced yet."
            )
            return
        result = pipeline.run(db, gameweek=gameweek)

    delivery = channel.send(result.report)
    logger.info(
        "Weekly pipeline for gw {} delivered via {} in {:.2f}s: {}",
        result.gameweek,
        delivery.channel,
        result.duration_seconds,
        delivery.detail,
    )
