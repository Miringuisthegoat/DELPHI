"""
Phase 12: pure translation functions, one `merged_gw.csv` row -> a
`HistoricalPlayerGameweekStats` field dict.

Deliberately free of database/session/network logic, matching the
convention set by `app.services.ingestion.mappers` - every mapping rule
is unit-testable against a plain dict/Series without a database or a
live GitHub fetch in the loop.

Phase 13: adds the four "defensive contribution" columns FPL introduced
partway through the dataset's lifetime (first appearing in the 2025-26
season's `merged_gw.csv`: `clearances_blocks_interceptions`, `tackles`,
`recoveries`, `defensive_contribution`). Seasons before 2025-26 simply
don't have these columns in their CSVs - `resolve_columns` treats them
as optional (not in the "required" list) and `_num()` already defaults
any absent column to 0, so older seasons remain valid, just correctly
reporting no defensive-contribution activity (because the rule didn't
exist yet, not because of missing data).
"""

from __future__ import annotations

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)

# vaastav's CSV has used a few different column names for the same thing
# across seasons. Each entry lists candidates in preference order; the
# first one present in the DataFrame wins.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "position": ("position",),
    "team": ("team",),
    "gameweek": ("GW", "round"),
    "minutes": ("minutes",),
    "goals_scored": ("goals_scored",),
    "assists": ("assists",),
    "expected_goals": ("expected_goals", "xG"),
    "expected_assists": ("expected_assists", "xA"),
    "clean_sheets": ("clean_sheets",),
    "goals_conceded": ("goals_conceded",),
    "saves": ("saves",),
    "own_goals": ("own_goals",),
    "penalties_saved": ("penalties_saved",),
    "yellow_cards": ("yellow_cards",),
    "red_cards": ("red_cards",),
    "penalties_missed": ("penalties_missed",),
    "bonus": ("bonus",),
    "bps": ("bps",),
    "total_points": ("total_points",),
    "value": ("value",),
    "ict_index": ("ict_index",),
    "influence": ("influence",),
    "creativity": ("creativity",),
    "threat": ("threat",),
    "source_xp": ("xP", "expected_points"),
    # --- Phase 13: defensive contribution (2025-26+ only) ---------------
    "clearances_blocks_interceptions": ("clearances_blocks_interceptions",),
    "tackles": ("tackles",),
    "recoveries": ("recoveries",),
    "defensive_contribution": ("defensive_contribution",),
}


def resolve_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """For each logical field, pick the first alias present in `df`.

    Returns a mapping of logical_name -> actual_column_name (or None if
    no alias is present in this season's CSV - the mapper then defaults
    that field rather than raising, since a missing optional column
    (e.g. no xG data pre-2021/22, or no defensive-contribution columns
    pre-2025-26) shouldn't abort the whole season.
    """
    resolved: dict[str, str | None] = {}
    for logical_name, aliases in _COLUMN_ALIASES.items():
        resolved[logical_name] = next((a for a in aliases if a in df.columns), None)
    missing_required = [
        name for name in ("name", "gameweek", "total_points") if resolved[name] is None
    ]
    if missing_required:
        raise ValueError(
            f"merged_gw.csv is missing required column(s) for: {missing_required} "
            "- cannot map this season's data."
        )
    return resolved


def _num(row: pd.Series, columns: dict[str, str | None], key: str, default: float = 0.0) -> float:
    col = columns.get(key)
    if col is None or col not in row:
        return default
    value = row[col]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def map_gw_row(
    row: pd.Series, columns: dict[str, str | None], season: str
) -> dict[str, object]:
    """Map one `merged_gw.csv` row onto `HistoricalPlayerGameweekStats` fields.

    Args:
        row: A single pandas row (Series) from the season's DataFrame.
        columns: The logical->actual column mapping from `resolve_columns`.
        season: The season string this row belongs to, e.g. "2023-24".
    """
    name_col = columns["name"]
    gw_col = columns["gameweek"]

    return {
        "season": season,
        "gameweek": int(row[gw_col]),
        "source_name": str(row[name_col]).strip(),
        "position": str(row[columns["position"]]).strip().upper()
        if columns["position"]
        else "MID",
        "team_name": str(row[columns["team"]]).strip() if columns["team"] else "",
        "minutes": int(_num(row, columns, "minutes")),
        "goals_scored": int(_num(row, columns, "goals_scored")),
        "assists": int(_num(row, columns, "assists")),
        "expected_goals": _num(row, columns, "expected_goals"),
        "expected_assists": _num(row, columns, "expected_assists"),
        "clean_sheets": int(_num(row, columns, "clean_sheets")),
        "goals_conceded": int(_num(row, columns, "goals_conceded")),
        "saves": int(_num(row, columns, "saves")),
        "own_goals": int(_num(row, columns, "own_goals")),
        "penalties_saved": int(_num(row, columns, "penalties_saved")),
        "yellow_cards": int(_num(row, columns, "yellow_cards")),
        "red_cards": int(_num(row, columns, "red_cards")),
        "penalties_missed": int(_num(row, columns, "penalties_missed")),
        "bonus": int(_num(row, columns, "bonus")),
        "bps": int(_num(row, columns, "bps")),
        "total_points": int(_num(row, columns, "total_points")),
        # vaastav's `value` is already in tenths-of-a-million, matching
        # `Player.now_cost` / `PlayerGameweekStats.price_at_gameweek`.
        "price_at_gameweek": int(_num(row, columns, "value")),
        "ict_index": _num(row, columns, "ict_index"),
        "influence": _num(row, columns, "influence"),
        "creativity": _num(row, columns, "creativity"),
        "threat": _num(row, columns, "threat"),
        "source_xp": (
            _num(row, columns, "source_xp") if columns.get("source_xp") else None
        ),
        # --- Phase 13: defensive contribution ---------------------------
        # 0 for pre-2025-26 seasons, where these columns don't exist yet -
        # correct behaviour, not a data gap (see module docstring).
        "clearances_blocks_interceptions": int(
            _num(row, columns, "clearances_blocks_interceptions")
        ),
        "tackles": int(_num(row, columns, "tackles")),
        "recoveries": int(_num(row, columns, "recoveries")),
        "defensive_contribution": int(_num(row, columns, "defensive_contribution")),
    }
