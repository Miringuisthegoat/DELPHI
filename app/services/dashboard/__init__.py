"""Dashboard package: assembles Phase 5-7 data into one view model for the Phase 8 UI."""

from app.services.dashboard.service import (
    CaptainSuggestion,
    DashboardService,
    DashboardView,
    FixtureTickerEntry,
    InjuryAlert,
    SquadRow,
)

__all__ = [
    "CaptainSuggestion",
    "DashboardService",
    "DashboardView",
    "FixtureTickerEntry",
    "InjuryAlert",
    "SquadRow",
]
