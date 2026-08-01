"""
Phase 9: APScheduler wiring for automatic weekly report generation.

`settings.enable_scheduler` / `settings.weekly_update_cron` have existed
since Phase 1 waiting for exactly this. When enabled, `start_scheduler()`
registers one cron job that builds the weekly report for "the current
gameweek" (the latest synced `SquadState`, same fallback Phase 8's
dashboard uses) and delivers it via `ConsoleDeliveryChannel` by default.

Deliberately conservative for a first pass:
* The job does **not** trigger a fresh FPL API sync or re-generate
  predictions itself - it reports on whatever Phase 4/5/6/7 have already
  produced. Chaining "sync -> predict -> optimize -> report" into one
  cron job is a natural next step, but bundling it here would mean one
  slow/failing step (e.g. the FPL API being down) silently breaks
  reporting too. Keeping this job read-only makes it safe to enable
  immediately.
* Only `ConsoleDeliveryChannel` is wired by default, since it's the only
  channel that works without any external account setup (see
  `app.services.reporting.delivery`). Swap `_delivery_channel()` once a
  real Telegram/Discord channel is implemented.
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from sqlalchemy import select

from app.core.config import settings
from app.db.session import session_scope
from app.models.squad import SquadState
from app.services.reporting import ConsoleDeliveryChannel, DeliveryChannel, WeeklyReportService

_scheduler: BackgroundScheduler | None = None


def _delivery_channel() -> DeliveryChannel:
    """The channel automatic weekly reports are sent to.

    Swap this for `TelegramDeliveryChannel(...)` / `DiscordDeliveryChannel(...)`
    once one of those is implemented and configured - everything else in
    this module is channel-agnostic.
    """
    return ConsoleDeliveryChannel()


def _resolve_current_gameweek(db) -> int | None:
    """The latest gameweek with a synced `SquadState`, or None if none yet."""
    return (
        db.execute(select(SquadState.gameweek).order_by(SquadState.gameweek.desc()))
        .scalars()
        .first()
    )


def generate_and_deliver_weekly_report() -> None:
    """The scheduled job body: build this gameweek's report and deliver it.

    Safe to call manually too (e.g. from a script or an API route) - it
    has no dependency on actually being invoked by APScheduler.
    """
    service = WeeklyReportService()
    channel = _delivery_channel()

    with session_scope() as db:
        gameweek = _resolve_current_gameweek(db)
        if gameweek is None:
            logger.warning(
                "Scheduled weekly report skipped: no squad state synced yet."
            )
            return
        report = service.build_report(db, gameweek=gameweek)

    result = channel.send(report)
    logger.info(
        "Weekly report for gw {} delivered via {}: {}",
        report.gameweek,
        result.channel,
        result.detail,
    )


def start_scheduler() -> BackgroundScheduler | None:
    """Start the background scheduler if `settings.enable_scheduler` is True.

    Returns the running `BackgroundScheduler`, or None if scheduling is
    disabled - callers (app startup) should treat None as "nothing to
    shut down later".
    """
    global _scheduler

    if not settings.enable_scheduler:
        logger.info("Scheduler disabled (ENABLE_SCHEDULER=false) - skipping.")
        return None

    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        generate_and_deliver_weekly_report,
        trigger=CronTrigger.from_crontab(settings.weekly_update_cron),
        id="delphi_weekly_report",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: weekly report job registered on cron '{}'.",
        settings.weekly_update_cron,
    )
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    """Shut down the background scheduler, if one is running."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped.")
