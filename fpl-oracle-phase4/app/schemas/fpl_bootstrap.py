"""
Typed schemas for the ``/bootstrap-static/`` endpoint.

This endpoint returns dozens of fields per player and several other
top-level collections (teams, events/gameweeks, element_types, etc.).
We deliberately model only the fields our system currently uses.

``model_config = ConfigDict(extra="ignore")`` on every model means:
  * FPL can add new fields at any time without breaking us.
  * If FPL *removes or renames* a field we rely on, Pydantic will raise
    a validation error immediately (fail loudly, not silently), which
    is exactly the signal ``FPLResponseParsingError`` is meant to
    surface to callers.

Field names intentionally mirror FPL's own (snake_case) naming rather
than being renamed to "nicer" Python names, so that mapping raw API
responses to these schemas — and later, comparing our code against
FPL API changes — stays a mechanical, low-error process. Rename to
domain-friendly names at the *service/model* layer, not here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FPLTeam(BaseModel):
    """A single Premier League club, as returned by bootstrap-static."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    short_name: str
    strength: int
    strength_overall_home: int
    strength_overall_away: int
    strength_attack_home: int
    strength_attack_away: int
    strength_defence_home: int
    strength_defence_away: int
    played: int = 0
    win: int = 0
    draw: int = 0
    loss: int = 0
    points: int = 0
    position: int | None = None
    unavailable: bool = False


class FPLElementType(BaseModel):
    """A player position (Goalkeeper / Defender / Midfielder / Forward)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    singular_name: str
    singular_name_short: str
    squad_select: int
    squad_min_play: int
    squad_max_play: int


class FPLEvent(BaseModel):
    """A single gameweek ("event" in FPL's terminology)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    deadline_time: datetime
    finished: bool
    is_previous: bool
    is_current: bool
    is_next: bool
    average_entry_score: int = 0
    highest_score: int | None = None
    most_selected: int | None = None
    most_transferred_in: int | None = None
    most_captained: int | None = None
    most_vice_captained: int | None = None
    transfers_made: int = 0


class FPLPlayer(BaseModel):
    """A single player ("element" in FPL's terminology).

    FPL's raw field is called ``element_type`` for position and
    ``team`` for the club id; both are kept as-is here (see module
    docstring) and translated to our domain models in the service layer.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    first_name: str
    second_name: str
    web_name: str
    team: int
    element_type: int

    now_cost: int
    """Price in tenths of a million, e.g. 125 == £12.5m."""

    cost_change_start: int = 0
    cost_change_event: int = 0
    transfers_in_event: int = 0
    transfers_out_event: int = 0
    """Transfers in/out *since the last gameweek deadline*, used as a
    short-term momentum signal (see `Player.price_trend`)."""


    selected_by_percent: str
    """FPL returns this as a string, e.g. "34.5" — cast to float downstream."""

    form: str
    points_per_game: str
    ep_this: str | None = None
    ep_next: str | None = None
    value_form: str = "0.0"
    value_season: str = "0.0"

    total_points: int = 0
    event_points: int = 0
    bonus: int = 0
    bps: int = 0

    minutes: int = 0
    goals_scored: int = 0
    assists: int = 0
    clean_sheets: int = 0
    goals_conceded: int = 0
    own_goals: int = 0
    penalties_saved: int = 0
    penalties_missed: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    saves: int = 0

    influence: str = "0.0"
    creativity: str = "0.0"
    threat: str = "0.0"
    ict_index: str = "0.0"

    expected_goals: str | None = Field(default=None)
    expected_assists: str | None = Field(default=None)
    expected_goal_involvements: str | None = Field(default=None)
    expected_goals_conceded: str | None = Field(default=None)

    status: str
    """One of 'a' (available), 'd' (doubtful), 'i' (injured), 's' (suspended),
    'u' (unavailable/left club), 'n' (not in squad)."""

    chance_of_playing_this_round: int | None = None
    chance_of_playing_next_round: int | None = None
    news: str = ""
    news_added: datetime | None = None

    dreamteam_count: int = 0
    in_dreamteam: bool = False


class FPLGameSettings(BaseModel):
    """The small subset of league-wide rules we actually consume.

    Sourcing squad size / budget / transfer limits from the API rather
    than hardcoding them means a rule change (e.g. a budget increase)
    doesn't require a code deploy.
    """

    model_config = ConfigDict(extra="ignore")

    squad_squadsize: int = 15
    squad_squadplay: int = 11
    squad_team_limit: int = 3
    """Max players allowed from a single Premier League club."""

    squad_total_spend: int = 1000
    """Starting budget in tenths of a million, e.g. 1000 == £100.0m."""

    transfers_cap: int | None = None


class FPLBootstrapStatic(BaseModel):
    """Top-level response shape of ``/bootstrap-static/``."""

    model_config = ConfigDict(extra="ignore")

    events: list[FPLEvent]
    teams: list[FPLTeam]
    element_types: list[FPLElementType]
    elements: list[FPLPlayer]
    game_settings: FPLGameSettings
