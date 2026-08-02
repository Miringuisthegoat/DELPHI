"""
Phase 10: `WeeklyPipelineService` - the single call that chains everything
Phases 5-9 already built into one end-to-end weekly run.

Why this exists
----------------
Up to Phase 9, "run the weekly workflow" meant calling four separate
scripts/endpoints by hand in the right order:

    1. python -m scripts.generate_predictions --gameweek N
    2. (squad sync, if not already done for N)
    3. python -m scripts.optimize_transfers --gameweek N   (implicitly,
       via the dashboard/report, which call the optimizer themselves)
    4. python -m scripts.generate_report --gameweek N [--send]

That's fine for development, but it's exactly the kind of manual,
easy-to-forget-a-step sequence that should be one function call before
relying on this for a real season. `WeeklyPipelineService.run()` is that
function call.

What it deliberately does NOT do
---------------------------------
* **No live FPL API sync.** Syncing bootstrap/fixtures/squad talks to an
  external, occasionally-down API and belongs to a distinct failure
  domain (network/schema errors) from the purely-local prediction ->
  optimize -> report chain. Bundling them would mean a slow/failing sync
  step silently breaks reporting too - the same reasoning Phase 9's
  scheduler job docstring already gives for keeping sync out of the
  automatic weekly report. Call `DataIngestionService`/`SquadSyncService`
  (or the corresponding CLI scripts) *before* this pipeline, same as
  today.
* **No new business logic.** Every number produced here comes from
  `DelphiPredictionEngine`, `TransferOptimizerService` (via
  `DashboardService`, inside `WeeklyReportService`), and
  `WeeklyReportService` itself - this class only sequences existing
  calls and packages their outputs together, so there is exactly one
  place that could disagree with the dashboard/report about "what
  DELPHI recommends" (there still is: `WeeklyReportService`).

Partial-failure handling
-------------------------
Optimization and evaluation are allowed to be "not ready yet" (no squad
synced, no previous gameweek to evaluate) without aborting the whole
run - `WeeklyReportService`/`DashboardService` already model those as
non-fatal empty states (see their docstrings), and this service simply
surfaces whatever they produce. Only prediction *generation* is treated
as fatal, since every downstream step depends on it having produced
something.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.engine import DelphiPredictionEngine, EvaluationSummary, GenerationSummary
from app.services.reporting import WeeklyReport, WeeklyReportService


@dataclass
class PipelineResult:
    """Everything one `WeeklyPipelineService.run()` call produced."""

    gameweek: int
    generation: GenerationSummary
    report: WeeklyReport
    evaluation: EvaluationSummary | None
    """Only populated if `evaluate_previous=True` was requested and a
    previous gameweek's `PlayerGameweekStats` were available to score
    against - otherwise `None`, not an error."""
    duration_seconds: float


class WeeklyPipelineService:
    """Runs the full local weekly workflow: predict -> optimize -> report.

    Stateless aside from the `Session` passed per call, matching every
    other `*Service` in this project.
    """

    def __init__(
        self,
        prediction_engine: DelphiPredictionEngine | None = None,
        report_service: WeeklyReportService | None = None,
    ) -> None:
        self._engine = prediction_engine or DelphiPredictionEngine()
        self._reports = report_service or WeeklyReportService()

    def run(
        self,
        db: Session,
        gameweek: int,
        horizons: tuple[int, ...] | None = None,
        evaluate_previous: bool = True,
    ) -> PipelineResult:
        """Generate predictions, then build the weekly report for `gameweek`.

        Args:
            db: Active SQLAlchemy session (caller commits, per the
                project's `session_scope()` convention).
            gameweek: The upcoming gameweek to plan for.
            horizons: Which horizons to (re)generate predictions for.
                Defaults to `settings.ml_default_horizons`.
            evaluate_previous: If True, also backfill actual outcomes for
                `gameweek - 1`'s horizon-1 predictions before reporting,
                so the report's "Prediction Accuracy" section (Phase 9)
                reflects the latest available result. Silently skipped
                (not an error) if `gameweek <= 1` or that gameweek's
                stats haven't been synced yet - `evaluate_gameweek`
                already tolerates zero matching rows.
        """
        started = time.perf_counter()
        horizons = horizons or settings.ml_default_horizons

        generation = self._engine.generate_for_gameweek(
            db, gameweek=gameweek, horizons=horizons
        )

        evaluation: EvaluationSummary | None = None
        if evaluate_previous and gameweek > 1:
            evaluation = self._engine.evaluate_gameweek(db, gameweek=gameweek - 1)
            if evaluation.predictions_evaluated == 0:
                # Nothing to score against yet (previous gameweek not
                # played/synced) - this is expected early in a season,
                # not a failure, so don't surface a hollow summary.
                evaluation = None

        report = self._reports.build_report(db, gameweek=gameweek)

        duration = round(time.perf_counter() - started, 3)
        logger.info(
            "Weekly pipeline for gw {} complete in {:.2f}s: {} predictions "
            "({} model), {} report section(s){}",
            gameweek,
            duration,
            generation.players_processed * len(horizons),
            generation.model_used,
            len(report.sections),
            f", evaluated {evaluation.predictions_evaluated} prior predictions"
            if evaluation is not None
            else "",
        )

        return PipelineResult(
            gameweek=gameweek,
            generation=generation,
            report=report,
            evaluation=evaluation,
            duration_seconds=duration,
        )
