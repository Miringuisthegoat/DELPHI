"""
Phase 9: `WeeklyReportService` - turns everything Phases 5-8 already know
into a single, readable prose report for one gameweek.

Design notes
------------
* **Reuses Phase 8's `DashboardView`, doesn't reload the world.** Every
  number a weekly report needs (projected points, captain, transfer
  suggestion, injury alerts, squad snapshot) is already assembled by
  `DashboardService.build_view()`. Duplicating those queries here would
  mean two places that could drift out of sync about "what DELPHI
  currently recommends" - so this service is a thin *formatter* over
  that same view model, not a second data-access layer.
* **Explainability lives in prose, not just numbers.** Matches the
  project's "never output a bare number" requirement (see Phase 5's
  `HeuristicPredictor` docstring) - every section either quotes an
  existing `reasoning` string (captain, transfer) or is itself written
  as a sentence, never a raw table with no narrative.
* **Prediction-accuracy section is genuinely optional.** Early in a
  season (or right after `evaluate_gameweek` hasn't been run yet for the
  previous week) there's nothing to report on learning progress. Rather
  than show a misleading "N/A" everywhere, the section is simply omitted
  when no evaluated predictions exist yet - `WeeklyReport.sections` only
  ever contains sections with real content.
* **One report object, two renderings.** `to_markdown()` is for the
  dashboard/API/file output; `to_plain_text()` strips markdown syntax for
  channels that don't render it (Telegram without `parse_mode=Markdown`,
  Discord's default view, SMS-style delivery). Both are built from the
  same `sections` list so they can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prediction import Prediction
from app.services.dashboard import DashboardService
from app.services.dashboard.service import DashboardView


@dataclass
class ReportSection:
    """One titled block of the report."""

    heading: str
    lines: list[str] = field(default_factory=list)


@dataclass
class WeeklyReport:
    """A fully-assembled gameweek report, ready to render or deliver."""

    gameweek: int
    generated_at: datetime
    headline: str
    sections: list[ReportSection] = field(default_factory=list)

    def to_markdown(self) -> str:
        parts = [f"# {self.headline}", ""]
        for section in self.sections:
            parts.append(f"## {section.heading}")
            parts.extend(section.lines)
            parts.append("")
        return "\n".join(parts).strip() + "\n"

    def to_plain_text(self) -> str:
        """A markdown-free rendering, suited to Telegram/Discord/SMS."""
        parts = [self.headline, ""]
        for section in self.sections:
            parts.append(section.heading.upper())
            parts.extend(f"- {line}" for line in section.lines)
            parts.append("")
        return "\n".join(parts).strip() + "\n"


class WeeklyReportService:
    """Builds a `WeeklyReport` for one gameweek from Phase 8's dashboard view."""

    def __init__(self, dashboard_service: DashboardService | None = None) -> None:
        self._dashboard = dashboard_service or DashboardService()

    def build_report(self, db: Session, gameweek: int) -> WeeklyReport:
        view = self._dashboard.build_view(db, gameweek=gameweek)
        generated_at = datetime.now(timezone.utc)

        if not view.has_squad:
            return WeeklyReport(
                gameweek=gameweek,
                generated_at=generated_at,
                headline=f"DELPHI Weekly Report - Gameweek {gameweek}",
                sections=[
                    ReportSection(
                        heading="Squad Not Synced",
                        lines=[view.optimization_error or "No squad state found yet."],
                    )
                ],
            )

        sections: list[ReportSection] = [self._squad_snapshot_section(view)]

        if view.has_predictions:
            sections.append(self._projection_section(view))
            if view.captain is not None:
                sections.append(self._captaincy_section(view))
            sections.append(self._transfer_section(view))
        else:
            sections.append(
                ReportSection(
                    heading="Predictions Not Generated Yet",
                    lines=[
                        view.optimization_error
                        or "Generate predictions for this gameweek to unlock "
                        "projected points, captaincy, and transfer suggestions."
                    ],
                )
            )

        if view.injury_alerts:
            sections.append(self._injury_section(view))

        accuracy_section = self._accuracy_section(db, gameweek)
        if accuracy_section is not None:
            sections.append(accuracy_section)

        headline = (
            f"DELPHI Weekly Report - Gameweek {gameweek}"
            + (
                f" (projected {view.projected_points:.1f} pts)"
                if view.has_predictions
                else ""
            )
        )

        return WeeklyReport(
            gameweek=gameweek,
            generated_at=generated_at,
            headline=headline,
            sections=sections,
        )

    # --- Section builders -----------------------------------------------------

    @staticmethod
    def _squad_snapshot_section(view: DashboardView) -> ReportSection:
        lines = [
            f"Bank: £{view.bank_millions:.1f}m",
            f"Squad value: £{view.squad_value_millions:.1f}m",
            f"Free transfers: {view.free_transfers}",
            f"Total points so far: {view.total_points}",
        ]
        if view.overall_rank is not None:
            lines.append(f"Overall rank: {view.overall_rank:,}")
        if view.chips_available:
            lines.append("Chips available: " + ", ".join(view.chips_available))
        if view.chip_played:
            lines.append(f"Chip played this gameweek: {view.chip_played}")
        return ReportSection(heading="Squad Snapshot", lines=lines)

    @staticmethod
    def _projection_section(view: DashboardView) -> ReportSection:
        return ReportSection(
            heading="Projected Points",
            lines=[f"DELPHI projects {view.projected_points:.1f} points this gameweek."],
        )

    @staticmethod
    def _captaincy_section(view: DashboardView) -> ReportSection:
        lines = [
            f"Captain: {view.captain.web_name} "
            f"({view.captain.predicted_points:.1f} projected pts)",
            view.captain.reasoning,
        ]
        if view.vice_captain is not None:
            lines.append(
                f"Vice captain: {view.vice_captain.web_name} "
                f"({view.vice_captain.predicted_points:.1f} projected pts)"
            )
        return ReportSection(heading="Captaincy", lines=lines)

    @staticmethod
    def _transfer_section(view: DashboardView) -> ReportSection:
        if view.optimization is None:
            return ReportSection(
                heading="Transfer Suggestion",
                lines=[
                    view.optimization_error
                    or "No transfer recommendation could be computed this week."
                ],
            )

        rec = view.optimization.recommended
        lines: list[str] = []
        if rec.transfers == 0:
            lines.append("Hold your squad - no affordable swap beats what you already own.")
        else:
            for out_p, in_p in zip(rec.players_out, rec.players_in):
                lines.append(
                    f"{out_p.web_name} (£{out_p.price_millions:.1f}m) -> "
                    f"{in_p.web_name} (£{in_p.price_millions:.1f}m)"
                )
        lines.append(rec.reasoning)
        return ReportSection(heading="Transfer Suggestion", lines=lines)

    @staticmethod
    def _injury_section(view: DashboardView) -> ReportSection:
        lines = []
        for alert in view.injury_alerts:
            chance = (
                f"{alert.chance_of_playing_next_round}% chance of playing next round"
                if alert.chance_of_playing_next_round is not None
                else "playing chance unknown"
            )
            news = f" - {alert.news}" if alert.news else ""
            lines.append(f"{alert.web_name} ({alert.status}, {chance}){news}")
        return ReportSection(heading="Injury / Availability Alerts", lines=lines)

    @staticmethod
    def _accuracy_section(db: Session, gameweek: int) -> ReportSection | None:
        """Summarise how well DELPHI's horizon-1 predictions did last gameweek.

        Only reports on gameweeks that have already been evaluated (see
        `DelphiPredictionEngine.evaluate_gameweek`) - if the previous
        gameweek hasn't been played/evaluated yet, there is nothing
        genuine to say about accuracy, so the section is omitted rather
        than showing misleading placeholder numbers.
        """
        previous_gameweek = gameweek - 1
        if previous_gameweek < 1:
            return None

        rows = db.execute(
            select(Prediction).where(
                Prediction.gameweek == previous_gameweek,
                Prediction.horizon == 1,
                Prediction.actual_points.is_not(None),
            )
        ).scalars().all()

        if not rows:
            return None

        errors = [abs(r.prediction_error) for r in rows if r.prediction_error is not None]
        if not errors:
            return None

        mae = sum(errors) / len(errors)
        best = min(rows, key=lambda r: abs(r.prediction_error or 0.0))
        worst = max(rows, key=lambda r: abs(r.prediction_error or 0.0))

        lines = [
            f"Gameweek {previous_gameweek}: {len(rows)} predictions evaluated, "
            f"mean absolute error {mae:.2f} points.",
            f"Closest call: player {best.player_id} "
            f"(predicted {best.predicted_points:.1f}, actual {best.actual_points:.1f}).",
            f"Biggest miss: player {worst.player_id} "
            f"(predicted {worst.predicted_points:.1f}, actual {worst.actual_points:.1f}).",
        ]
        return ReportSection(heading="Prediction Accuracy (Learning Log)", lines=lines)
