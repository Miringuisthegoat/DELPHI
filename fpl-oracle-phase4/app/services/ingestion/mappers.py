"""
Pure translation functions: typed FPL API schemas -> ORM model field dicts.

Deliberately free of any database or session logic (see `service.py` for
that), so every mapping rule can be unit-tested in isolation and reasoned
about without a database in the loop. Each function returns a plain
``dict`` of column values rather than an ORM instance, so callers can
choose whether to construct a new row or update an existing one with the
same values (the "upsert" decision belongs to the service layer, not here).
"""

from __future__ import annotations

import logging

from enums import InjuryStatus, Position
from app.schemas.fpl_bootstrap import FPLElementType, FPLPlayer, FPLTeam
from app.schemas.fpl_element_summary import FPLElementHistory
from app.schemas.fpl_fixtures import FPLFixture
from app.schemas.fpl_live import FPLLiveElement

logger = logging.getLogger(__name__)

# FPL's `status` values are 'a', 'd', 'i', 's', 'u', and (for players not
# currently in a squad at all, e.g. departed the league) 'n'. Our
# `InjuryStatus` enum models the five FPL uses for *rostered* players;
# 'n' is folded into UNAVAILABLE since, from a squad-planning point of
# view, they behave identically (not selectable).
_STATUS_MAP: dict[str, InjuryStatus] = {
    "a": InjuryStatus.AVAILABLE,
    "d": InjuryStatus.DOUBTFUL,
    "i": InjuryStatus.INJURED,
    "s": InjuryStatus.SUSPENDED,
    "u": InjuryStatus.UNAVAILABLE,
    "n": InjuryStatus.UNAVAILABLE,
}


def _safe_float(value: str | float | None, default: float = 0.0) -> float:
    """Parse one of FPL's many "numbers returned as strings" fields.

    FPL is inconsistent about whether a given numeric field is JSON number
    or JSON string across endpoints and seasons, so every call site would
    otherwise need its own try/except. Malformed/missing values fall back
    to `default` rather than raising, since a single bad field (e.g. a
    player with no minutes yet, and thus an empty ICT index) should never
    abort an entire sync.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Could not parse numeric FPL field %r, using default", value)
        return default


def build_position_map(element_types: list[FPLElementType]) -> dict[int, Position]:
    """Build the `element_type` id -> `Position` lookup from bootstrap-static.

    FPL's `element_types[i].singular_name_short` values ("GKP", "DEF",
    "MID", "FWD") happen to match our `Position` enum values exactly, but
    we resolve this dynamically (rather than hardcoding `{1: GKP, ...}`)
    so that if FPL ever adds a new outfield sub-position, ingestion logs a
    clear warning instead of silently mis-classifying players.
    """
    mapping: dict[int, Position] = {}
    for element_type in element_types:
        try:
            mapping[element_type.id] = Position(element_type.singular_name_short)
        except ValueError:
            logger.warning(
                "Unrecognised FPL element_type %s (%s) - players with this "
                "type will be skipped during ingestion until the Position "
                "enum is updated.",
                element_type.id,
                element_type.singular_name_short,
            )
    return mapping


def map_team(team: FPLTeam) -> dict[str, object]:
    """Map a `FPLTeam` payload onto `Team` model column values."""
    return {
        "id": team.id,
        "name": team.name,
        "short_name": team.short_name,
        "strength_overall_home": team.strength_overall_home,
        "strength_overall_away": team.strength_overall_away,
        "strength_attack_home": team.strength_attack_home,
        "strength_attack_away": team.strength_attack_away,
        "strength_defence_home": team.strength_defence_home,
        "strength_defence_away": team.strength_defence_away,
        # `strength` is FPL's single overall rating; used as the shared
        # attack/defence fallback our prediction engine can key off of
        # before it has anything more specific to go on.
        "strength_attack": team.strength_attack_home,
        "strength_defence": team.strength_defence_home,
    }


def map_player(
    player: FPLPlayer, position_map: dict[int, Position]
) -> dict[str, object] | None:
    """Map a `FPLPlayer` payload onto `Player` model column values.

    Returns:
        The field dict, or ``None`` if `player.element_type` has no known
        `Position` mapping (see `build_position_map`) - the caller should
        skip persisting this player and count it as a failure.
    """
    position = position_map.get(player.element_type)
    if position is None:
        return None

    try:
        status = InjuryStatus(_STATUS_MAP[player.status])
    except KeyError:
        logger.warning(
            "Unrecognised FPL status %r for player %s, defaulting to AVAILABLE",
            player.status,
            player.id,
        )
        status = InjuryStatus.AVAILABLE

    return {
        "id": player.id,
        "first_name": player.first_name,
        "second_name": player.second_name,
        "web_name": player.web_name,
        "team_id": player.team,
        "position": position,
        "now_cost": player.now_cost,
        "ownership_percent": _safe_float(player.selected_by_percent),
        "price_trend": float(player.transfers_in_event - player.transfers_out_event),
        "status": status,
        "chance_of_playing_next_round": player.chance_of_playing_next_round,
        "news": player.news or None,
        "is_active": True,
    }


def map_fixture(fixture: FPLFixture) -> dict[str, object]:
    """Map an `FPLFixture` payload onto `Fixture` model column values."""
    return {
        "id": fixture.id,
        "gameweek": fixture.event,
        "home_team_id": fixture.team_h,
        "away_team_id": fixture.team_a,
        "home_difficulty": fixture.team_h_difficulty,
        "away_difficulty": fixture.team_a_difficulty,
        "kickoff_time": fixture.kickoff_time,
        "finished": fixture.finished,
        "home_score": fixture.team_h_score,
        "away_score": fixture.team_a_score,
    }


def map_history_row(player_id: int, row: FPLElementHistory) -> dict[str, object]:
    """Map one `FPLElementHistory` row onto `PlayerGameweekStats` column values.

    This is the primary source for *backfilling* historical per-gameweek
    stats: unlike the live-gameweek endpoint, `element-summary` history
    rows include the player's price and the fixture they played in for
    that specific gameweek.

    Note on `ownership_percent` and `form`: FPL's history endpoint reports
    `selected` as a raw manager count, not a percentage, and doesn't
    report historical `form` at all (form is only ever "as of now"). Both
    are left at 0.0 here rather than guessed; a future enrichment pass can
    backfill `ownership_percent` from the total number of managers in that
    gameweek if this ever becomes decision-relevant.
    """
    started = bool(row.starts) if row.starts is not None else row.minutes > 0

    return {
        "player_id": player_id,
        "gameweek": row.round,
        "fixture_id": row.fixture,
        "minutes": row.minutes,
        "started": started,
        "goals_scored": row.goals_scored,
        "assists": row.assists,
        "expected_goals": _safe_float(row.expected_goals),
        "expected_assists": _safe_float(row.expected_assists),
        "expected_goal_involvements": _safe_float(row.expected_goal_involvements),
        "clean_sheets": row.clean_sheets,
        "goals_conceded": row.goals_conceded,
        "expected_goals_conceded": _safe_float(row.expected_goals_conceded),
        "saves": row.saves,
        "own_goals": row.own_goals,
        "penalties_saved": row.penalties_saved,
        "yellow_cards": row.yellow_cards,
        "red_cards": row.red_cards,
        "penalties_missed": row.penalties_missed,
        "bonus": row.bonus,
        "bps": row.bps,
        "total_points": row.total_points,
        "price_at_gameweek": row.value,
        "ownership_percent": 0.0,
        "form": 0.0,
        "ict_index": _safe_float(row.ict_index),
        "influence": _safe_float(row.influence),
        "creativity": _safe_float(row.creativity),
        "threat": _safe_float(row.threat),
    }


def map_live_element(
    element: FPLLiveElement,
    *,
    price_at_gameweek: int,
    ownership_percent: float,
    form: float,
) -> dict[str, object]:
    """Map one `FPLLiveElement` entry onto `PlayerGameweekStats` column values.

    Used for near-real-time updates to the *current* gameweek, where the
    live endpoint is available well before `element-summary` history is
    fully finalised. The live endpoint doesn't carry price/ownership/form,
    so those are supplied by the caller (typically read straight from the
    already-synced `Player` row as a same-day approximation) rather than
    guessed here.

    `fixture_id` is intentionally omitted (set by the caller if known):
    the live payload doesn't include per-element fixture ids directly.
    """
    stats = element.stats
    return {
        "player_id": element.id,
        "minutes": stats.minutes,
        "started": stats.minutes > 0,
        "goals_scored": stats.goals_scored,
        "assists": stats.assists,
        "clean_sheets": stats.clean_sheets,
        "goals_conceded": stats.goals_conceded,
        "saves": stats.saves,
        "own_goals": stats.own_goals,
        "penalties_saved": stats.penalties_saved,
        "yellow_cards": stats.yellow_cards,
        "red_cards": stats.red_cards,
        "penalties_missed": stats.penalties_missed,
        "bonus": stats.bonus,
        "bps": stats.bps,
        "total_points": stats.total_points,
        "price_at_gameweek": price_at_gameweek,
        "ownership_percent": ownership_percent,
        "form": form,
        "ict_index": _safe_float(stats.ict_index),
        "influence": _safe_float(stats.influence),
        "creativity": _safe_float(stats.creativity),
        "threat": _safe_float(stats.threat),
    }
