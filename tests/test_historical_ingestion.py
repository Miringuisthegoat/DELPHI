"""Tests for Phase 12: `app.services.historical`.

Mapping/matching logic is tested directly against in-memory pandas
DataFrames / `Player` objects - no real GitHub fetch. `HistoricalIngestionService.ingest_season`
is exercised with a monkeypatched fetcher so the full map->match->upsert
path runs against the same throwaway in-memory SQLite database every
other test file uses, without any network access.
"""

from __future__ import annotations

import pandas as pd
import pytest

from enums import InjuryStatus, Position
from app.models.player import Player
from app.models.player_stats_historical import HistoricalPlayerGameweekStats
from app.models.team import Team
from app.services.historical.fetcher import HistoricalFetchError
from app.services.historical.mappers import map_gw_row, resolve_columns
from app.services.historical.name_matcher import PlayerNameMatcher
from app.services.historical.service import HistoricalIngestionService


def _team(db, team_id: int, name: str) -> Team:
    team = Team(id=team_id, name=name, short_name=name[:3].upper())
    db.add(team)
    db.flush()
    return team


def _player(db, player_id: int, team_id: int, first: str, second: str, web: str) -> Player:
    player = Player(
        id=player_id,
        first_name=first,
        second_name=second,
        web_name=web,
        team_id=team_id,
        position=Position.MID,
        now_cost=80,
        status=InjuryStatus.AVAILABLE,
        is_active=True,
    )
    db.add(player)
    db.flush()
    return player


_SAMPLE_CSV = pd.DataFrame(
    [
        {
            "name": "Mohamed Salah",
            "position": "MID",
            "team": "Liverpool",
            "GW": 1,
            "minutes": 90,
            "goals_scored": 2,
            "assists": 1,
            "total_points": 15,
            "value": 130,
            "xG": 1.8,
            "xA": 0.4,
        },
        {
            "name": "Some Departed Player",
            "position": "FWD",
            "team": "Watford",
            "GW": 1,
            "minutes": 90,
            "goals_scored": 0,
            "assists": 0,
            "total_points": 2,
            "value": 45,
        },
    ]
)


class TestMappers:
    def test_resolve_columns_finds_aliases(self):
        columns = resolve_columns(_SAMPLE_CSV)
        assert columns["gameweek"] == "GW"
        assert columns["expected_goals"] == "xG"

    def test_resolve_columns_raises_on_missing_required(self):
        broken = _SAMPLE_CSV.drop(columns=["total_points"])
        with pytest.raises(ValueError):
            resolve_columns(broken)

    def test_map_gw_row_maps_expected_fields(self):
        columns = resolve_columns(_SAMPLE_CSV)
        row = _SAMPLE_CSV.iloc[0]
        fields = map_gw_row(row, columns, season="2023-24")

        assert fields["season"] == "2023-24"
        assert fields["source_name"] == "Mohamed Salah"
        assert fields["total_points"] == 15
        assert fields["price_at_gameweek"] == 130
        assert fields["source_xp"] is None or isinstance(fields["source_xp"], float)

    def test_map_gw_row_defaults_missing_optional_columns(self):
        minimal = pd.DataFrame(
            [{"name": "Old Player", "GW": 1, "total_points": 4}]
        )
        columns = resolve_columns(minimal)
        fields = map_gw_row(minimal.iloc[0], columns, season="2018-19")

        assert fields["minutes"] == 0
        assert fields["expected_goals"] == 0.0
        assert fields["source_xp"] is None


class TestNameMatcher:
    def test_exact_web_name_match(self, db_session):
        team = _team(db_session, 1, "Liverpool")
        _player(db_session, 1, team.id, "Mohamed", "Salah", "Salah")

        matcher = PlayerNameMatcher(db_session.query(Player).all())
        result = matcher.match("Salah")

        assert result.player_id == 1
        assert result.method == "exact_web_name"
        assert result.confidence == 1.0

    def test_exact_full_name_match(self, db_session):
        team = _team(db_session, 1, "Liverpool")
        _player(db_session, 1, team.id, "Mohamed", "Salah", "M.Salah")

        matcher = PlayerNameMatcher(db_session.query(Player).all())
        result = matcher.match("Mohamed Salah")

        assert result.player_id == 1
        assert result.method == "exact_full_name"

    def test_fuzzy_match_above_threshold(self, db_session):
        team = _team(db_session, 1, "Spurs")
        _player(db_session, 1, team.id, "Heung-Min", "Son", "Son")

        matcher = PlayerNameMatcher(db_session.query(Player).all())
        result = matcher.match("Heung Min Son")

        assert result.player_id == 1
        assert result.method == "fuzzy"
        assert result.confidence >= 0.88

    def test_no_match_returns_unmatched(self, db_session):
        team = _team(db_session, 1, "Liverpool")
        _player(db_session, 1, team.id, "Mohamed", "Salah", "Salah")

        matcher = PlayerNameMatcher(db_session.query(Player).all())
        result = matcher.match("Completely Different Name")

        assert result.player_id is None
        assert result.method == "unmatched"


class TestHistoricalIngestionService:
    def test_ingest_season_upserts_matched_and_unmatched_rows(
        self, db_session, monkeypatch
    ):
        team = _team(db_session, 1, "Liverpool")
        _player(db_session, 1, team.id, "Mohamed", "Salah", "Salah")
        db_session.commit()

        service = HistoricalIngestionService()
        monkeypatch.setattr(
            service._fetcher, "fetch_season", lambda season: _SAMPLE_CSV
        )

        result = service.ingest_season(db_session, season="2023-24")
        db_session.commit()

        assert result.created == 2
        assert result.matched == 1
        assert result.unmatched == 1
        assert result.match_rate == 0.5

        rows = db_session.query(HistoricalPlayerGameweekStats).all()
        assert len(rows) == 2
        salah_row = next(r for r in rows if r.source_name == "Mohamed Salah")
        assert salah_row.matched_player_id == 1
        assert salah_row.match_method == "exact_full_name"

        departed_row = next(
            r for r in rows if r.source_name == "Some Departed Player"
        )
        assert departed_row.matched_player_id is None

    def test_rerun_updates_not_duplicates(self, db_session, monkeypatch):
        team = _team(db_session, 1, "Liverpool")
        _player(db_session, 1, team.id, "Mohamed", "Salah", "Salah")
        db_session.commit()

        service = HistoricalIngestionService()
        monkeypatch.setattr(
            service._fetcher, "fetch_season", lambda season: _SAMPLE_CSV
        )

        service.ingest_season(db_session, season="2023-24")
        db_session.commit()
        result = service.ingest_season(db_session, season="2023-24")
        db_session.commit()

        assert result.created == 0
        assert result.updated == 2
        assert db_session.query(HistoricalPlayerGameweekStats).count() == 2

    def test_fetch_failure_is_collected_not_raised(self, db_session, monkeypatch):
        service = HistoricalIngestionService()

        def _raise(season):
            raise HistoricalFetchError(f"no data for {season}")

        monkeypatch.setattr(service._fetcher, "fetch_season", _raise)

        result = service.ingest_season(db_session, season="1999-00")

        assert result.failed == 1
        assert result.processed == 0
        assert "1999-00" in result.errors[0] or "no data" in result.errors[0]
