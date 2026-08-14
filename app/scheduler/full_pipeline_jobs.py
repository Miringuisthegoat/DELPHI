"""
Full end-to-end weekly job: live FPL sync -> squad sync -> predict/evaluate
-> optimize -> report -> deliver, all in one scheduled call.

This supersedes Phase 9's report-only job (`app.scheduler.jobs`) and Phase
10's opt-in predict+report job (`app.scheduler.pipeline_jobs`) - neither of
those touch the live FPL API. This one does, deliberately, since the goal
now is zero manual steps once the season is live.

Design notes
------------
* **Gameweek is resolved from the live FPL API, not from `SquadState`.**
  Every previous scheduled job fell back to "the latest synced
  `SquadState`" - which is circular before the first sync ever happens.
  This job instead asks bootstrap-static which gameweek is `is_current`
  (or `is_next`, pre-deadline) and plans for that one.
* **Squad sync failure is non-fatal.** Before a gameweek's deadline has
  passed, `/entry/{id}/event/{gw}/picks/` 404s - expected behaviour in
  the days before a deadline, not an error worth aborting the whole job
  over. If it fails, predictions/optimization/report still run against
  whatever squad state (if any) already exists, and the report itself
  explains a missing squad rather than crashing (Phase 8/9's existing
  empty-state handling).
* **Bootstrap/fixtures sync failure IS fatal for this run.** Without
  fresh player/fixture data there's nothing trustworthy to predict from,
  so the job logs and returns rather than running stale predictions.
* **Sync-only, then a single `session_scope()` per phase** - matches
  every other service's convention in this project (fetch via the async
  API client, then persist inside its own transaction).
"""

from __future__ import annotations

import asyncio

from loguru import logger

from app.core.config import settings
from app.db.session import session_scope
from app.services.fpl_api import FPLAPIClient, FPLAPIError
from app.services.ingestion import DataIngestionService
from app.services.pipeline import WeeklyPipelineService
from app.services.reporting import (
    ConsoleDeliveryChannel,
    DeliveryChannel,
    TelegramDeliveryChannel,
)
from app.services.squad import SquadSyncService


def _delivery_channel() -> DeliveryChannel:
    """Telegram if configured, otherwise fall back to console logging."""
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramDeliveryChannel()
    return ConsoleDeliveryChannel()


async def _run_full_pipeline() -> None:
    ingestion = DataIngestionService()
    squad_sync = SquadSyncService()
    pipeline = WeeklyPipelineService()
    channel = _delivery_channel()

    async with FPLAPIClient() as client:
        # --- 1. Sync bootstrap-static + fixtures (fatal on failure) ---
        try:
            bootstrap = await client.get_bootstrap_static()
            fixtures = await client.get_fixtures()
        except FPLAPIError as exc:
            logger.error("Scheduled full pipeline: FPL API fetch failed, aborting: {}", exc)
            return

        with session_scope() as db:
            sync_summary = ingestion.sync_full_bootstrap(db, bootstrap, fixtures)
        logger.info(
            "Scheduled data sync: {} teams, {} players, {} fixtures processed ({} failures)",
            sync_summary.teams.processed,
            sync_summary.players.processed,
            sync_summary.fixtures.processed,
            sync_summary.teams.failed + sync_summary.players.failed + sync_summary.fixtures.failed,
        )

        # --- 2. Resolve the gameweek to plan for, from live FPL data ---
        gameweek = next((e.id for e in bootstrap.events if e.is_current), None)
        if gameweek is None:
            gameweek = next((e.id for e in bootstrap.events if e.is_next), None)

        if gameweek is None:
            logger.warning(
                "Scheduled full pipeline: could not resolve a current/next "
                "gameweek from bootstrap-static (season not started yet?) - skipping."
            )
            return

        # --- 3. Sync my squad for that gameweek (non-fatal on failure) ---
        if settings.fpl_team_id is not None:
            try:
                picks_payload = await client.get_entry_event_picks(
                    entry_id=settings.fpl_team_id, event_id=gameweek
                )
                with session_scope() as db:
                    squad_result = squad_sync.sync_from_fpl_payloads(
                        db, gameweek=gameweek, picks_payload=picks_payload
                    )
                logger.info(
                    "Scheduled squad sync for gw {}: {} created, {} updated, {} removed, "
                    "{} free transfer(s)",
                    gameweek,
                    squad_result.players_created,
                    squad_result.players_updated,
                    squad_result.players_removed,
                    squad_result.free_transfers,
                )
            except FPLAPIError as exc:
                # Expected before this gameweek's deadline has passed (picks
                # aren't published yet) - not fatal. Report will explain a
                # missing/stale squad rather than crash.
                logger.warning(
                    "Scheduled squad sync skipped for gw {} (picks not available yet?): {}",
                    gameweek,
                    exc,
                )
        else:
            logger.warning("FPL_TEAM_ID not set in .env - skipping squad sync step.")

    # --- 4. Predict -> evaluate previous gw -> report ---
    with session_scope() as db:
        result = pipeline.run(db, gameweek=gameweek)

    # --- 5. Deliver ---
    delivery = channel.send(result.report)
    logger.info(
        "Full weekly pipeline for gw {} delivered via {} in {:.2f}s: {} model, "
        "{} predictions created/{} updated, {}",
        result.gameweek,
        delivery.channel,
        result.duration_seconds,
        result.generation.model_used,
        result.generation.predictions_created,
        result.generation.predictions_updated,
        delivery.detail,
    )


def generate_full_weekly_pipeline() -> None:
    """Scheduled job entrypoint - APScheduler needs a sync callable, so this
    just drives the async chain above. Safe to call manually too (script,
    API route, or a scheduler job)."""
    asyncio.run(_run_full_pipeline())