"""
Centralised registry of official Fantasy Premier League API endpoints.

Keeping every URL in one place means that if FPL ever changes its API
(paths have shifted before), only this file needs to change — nothing
in the client, schemas, or callers needs to know the raw URL shape.

Reference: https://fantasy.premierleague.com/api/
No official docs exist; this is the community-documented endpoint set.
"""

from __future__ import annotations

FPL_BASE_URL = "https://fantasy.premierleague.com/api"

BOOTSTRAP_STATIC = "/bootstrap-static/"
"""All players, teams, gameweeks (events), game settings, and phases.
This is the single largest and most important endpoint — almost every
other part of the system (predictions, optimizer, dashboard) is seeded
from data returned here."""

FIXTURES = "/fixtures/"
"""All fixtures for the season, each with a difficulty rating (FDR) per
side. Supports an optional ?event={gw} query param to filter to one
gameweek."""

EVENT_LIVE = "/event/{event_id}/live/"
"""Live, gameweek-scoped stats for every player who has played at least
one minute (goals, assists, bonus, BPS, etc.) for a given gameweek."""

ELEMENT_SUMMARY = "/element-summary/{element_id}/"
"""Per-player detail: full history of past gameweeks this season,
history_past (previous seasons), and the player's upcoming fixtures."""

ENTRY = "/entry/{entry_id}/"
"""A specific manager's (i.e. team's) overall summary — used later when
we sync the user's own FPL squad rather than only the global player pool."""

ENTRY_EVENT_PICKS = "/entry/{entry_id}/event/{event_id}/picks/"
"""A specific manager's squad picks for a given gameweek, including
chip played, transfers made, and points breakdown."""

ENTRY_TRANSFERS = "/entry/{entry_id}/transfers/"
"""Full transfer history for a specific manager across the season."""


def build_url(path_template: str, **path_params: int | str) -> str:
    """Build a full FPL API URL from a path template and its parameters.

    Args:
        path_template: One of the endpoint constants above, e.g.
            ``ELEMENT_SUMMARY``.
        **path_params: Values to substitute into the template's
            ``{placeholders}``, e.g. ``element_id=123``.

    Returns:
        The fully-qualified URL.

    Example:
        >>> build_url(ELEMENT_SUMMARY, element_id=123)
        'https://fantasy.premierleague.com/api/element-summary/123/'
    """
    return f"{FPL_BASE_URL}{path_template.format(**path_params)}"
