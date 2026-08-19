"""Tests for Phase 13: defensive contribution scoring wiring.

Covers three layers:
1. `app.services.ingestion.mappers` - live/history FPL API rows carry
   the four new fields through to `PlayerGameweekStats` field dicts.
2. `app.services.historical.mappers` - vaastav CSV rows carry the same
   fields through (when present) or default to 0 (when absent, i.e.
   any season before 2025-26).
3. `app.ml.features.PlayerFeatureBuilder` - rolling averages are
   computed correctly from `PlayerGameweekStats` history, including the
   case where older rows in the window predate the rule and are 0.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.ml.features import FEATURE_NAMES, PlayerFeatureBuilder
from app.models.enums import InjuryStatus, Position
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats
from app.models.team import Team
from app.schemas.fpl_element_summary import FPLElementHistory
from app.schemas.fpl_live import FPLLiveElement, FPLLiveStats
from app.services.historical.mappers import map_gw_row, resolve_columns
from app.services.ingestion.mappers import map_history_row, map_live_element


def _team(db, team_id: int, name: str) -> Team:
    team = Team(id=team_id, name=name, short_name=name[:3].upper())
    db.add(team)
    db.flush()
    return team


def _player(db, player_id: int, team_id: int) -> Player:
    player = Player(
        id=player_id,
        first_name="Test",
        second_name=f"Player{player_id}",
        web_name=f"Player{player_id}",
        team_id=team_id,
        position=Position.DEF,
        now_cost=50,
        status=InjuryStatus.AVAILABLE,
        is_active=True,
    )
    db.add(player)
    db.flush()
    return player


class TestLiveAndHistoryMappers:
    def test_map_history_row_carries_defensive_contribution_fields(self):
        row = FPLElementHistory(
            element=1,
            fixture=1,
            opponent_team=2,
            total_points=8,
            was_home=True,
            kickoff_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
            round=1,
            minutes=90,
            value=50,
            clearances_blocks_interceptions=14,
            tackles=3,
            recoveries=6,
            defensive_contribution=2,
        )
        fields = map_history_row(player_id=1, row=row)

        assert fields["clearances_blocks_interceptions"] == 14
        assert fields["tackles"] == 3
        assert fields["recoveries"] == 6
        assert fields["defensive_contribution"] == 2

    def test_map_history_row_defaults_when_absent(self):
        # Old-season-shaped payload: FPL API itself won't send these
        # fields for gameweeks before the rule existed, so the schema
        # default (0) should apply.
        row = FPLElementHistory(
            element=1,
            fixture=1,
            opponent_team=2,
            total_points=6,
            was_home=False,
            kickoff_time=None,
            round=1,
            value=50,
        )
        fields = map_history_row(player_id=1, row=row)

        assert fields["clearances_blocks_interceptions"] == 0
        assert fields["defensive_contribution"] == 0

    def test_map_live_element_carries_defensive_contribution_fields(self):
        element = FPLLiveElement(
            id=1,
            stats=FPLLiveStats(
                minutes=90,
                total_points=8,
                clearances_blocks_interceptions=11,
                tackles=4,
                recoveries=7,
                defensive_contribution=2,
            ),
        )
        fields = map_live_element(
            element, price_at_gameweek=50, ownership_percent=10.0, form=3.0
        )

        assert fields["clearances_blocks_interceptions"] == 11
        assert fields["tackles"] == 4
        assert fields["recoveries"] == 7
        assert fields["defensive_contribution"] == 2


class TestHistoricalCsvMapper:
    def test_2025_26_style_csv_maps_defensive_contribution(self):
        df = pd.DataFrame(
            [
                {
                    "name": "Test Defender",
                    "position": "DEF",
                    "team": "Sunderland",
                    "GW": 1,
                    "total_points": 8,
                    "value": 45,
                    "clearances_blocks_interceptions": 14,
                    "tackles": 3,
                    "recoveries": 6,
                    "defensive_contribution": 2,
                }
            ]
        )
        columns = resolve_columns(df)
        fields = map_gw_row(df.iloc[0], columns, season="2025-26")

        assert fields["clearances_blocks_interceptions"] == 14
        assert fields["defensive_contribution"] == 2

    def test_pre_2025_26_csv_defaults_to_zero(self):
        # No CBI/tackle/recoveries/defensive_contribution columns at all -
        # matches every season's CSV before 2025-26.
        df = pd.DataFrame(
            [
                {
                    "name": "Old Season Defender",
                    "position": "DEF",
                    "team": "Arsenal",
                    "GW": 1,
                    "total_points": 6,
                    "value": 55,
                }
            ]
        )
        columns = resolve_columns(df)
        fields = map_gw_row(df.iloc[0], columns, season="2022-23")

        assert fields["clearances_blocks_interceptions"] == 0
        assert fields["tackles"] == 0
        assert fields["recoveries"] == 0
        assert fields["defensive_contribution"] == 0


class TestFeatureVectorDefensiveContribution:
    def test_feature_names_include_defensive_contribution_columns(self):
        for name in (
            "cbi_avg_5",
            "tackles_avg_5",
            "recoveries_avg_5",
            "defensive_contribution_avg_5",
        ):
            assert name in FEATURE_NAMES

    def test_rolling_average_computed_from_history(self, db_session):
        team = _team(db_session, 1, "Sunderland")
        player = _player(db_session, 1, team.id)

        for gw, cbi, tackles, recoveries, dc in [
            (1, 10, 2, 4, 0),
            (2, 14, 3, 6, 2),
            (3, 12, 3, 5, 2),
        ]:
            db_session.add(
                PlayerGameweekStats(
                    player_id=player.id,
                    gameweek=gw,
                    minutes=90,
                    total_points=6,
                    clearances_blocks_interceptions=cbi,
                    tackles=tackles,
                    recoveries=recoveries,
                    defensive_contribution=dc,
                )
            )
        db_session.flush()

        vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=4)

        assert vector.cbi_avg_5 == (10 + 14 + 12) / 3
        assert vector.tackles_avg_5 == (2 + 3 + 3) / 3
        assert vector.recoveries_avg_5 == (4 + 6 + 5) / 3
        assert vector.defensive_contribution_avg_5 == (0 + 2 + 2) / 3

    def test_pre_rule_history_contributes_zero_not_missing(self, db_session):
        """Rows from before the rule existed still have the columns
        (defaulted to 0 by the model), so they correctly dilute the
        rolling average toward 0 rather than being excluded/NaN."""
        team = _team(db_session, 1, "Arsenal")
        player = _player(db_session, 1, team.id)

        # Simulates 3 gameweeks of pre-2025-26-style data: no defensive
        # contribution columns populated (all default to 0).
        for gw in (1, 2, 3):
            db_session.add(
                PlayerGameweekStats(
                    player_id=player.id, gameweek=gw, minutes=90, total_points=5
                )
            )
        db_session.flush()

        vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=4)

        assert vector.cbi_avg_5 == 0.0
        assert vector.defensive_contribution_avg_5 == 0.0

    def test_cold_start_has_zero_defensive_contribution_features(self, db_session):
        team = _team(db_session, 1, "Arsenal")
        player = _player(db_session, 1, team.id)

        vector = PlayerFeatureBuilder().build(db_session, player, target_gameweek=1)

        assert vector.has_history is False
        assert vector.cbi_avg_5 == 0.0
        assert vector.tackles_avg_5 == 0.0
        assert vector.recoveries_avg_5 == 0.0
        assert vector.defensive_contribution_avg_5 == 0.0
        assert len(vector.to_row()) == len(FEATURE_NAMES)
