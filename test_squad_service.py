"""
Unit tests for Phase 7: `app.services.squad`.

No real FPL API calls - `picks_payload` dicts are constructed by hand to
match the shape `FPLAPIClient.get_entry_event_picks()` returns, exercising
`SquadSyncService` against the same throwaway in-memory SQLite database
used by every other phase's tests (see `tests/conftest.py`).
"""

from __future__ import annotations

from app.models.enums import InjuryStatus, Position
from app.models.player import Player
from app.models.squad import SquadState
from app.models.team import Team
from app.models.transfer import TransferHistory
from app.services.squad.mappers import compute_chips_available, compute_free_transfers
from app.services.squad.service import SquadSyncService


def _team(db, team_id: int, name: str) -> Team:
    team = Team(id=team_id, name=name, short_name=name[:3].upper())
    db.add(team)
    db.flush()
    return team


def _player(db, player_id: int, team_id: int, now_cost: int = 80) -> Player:
    player = Player(
        id=player_id,
        first_name="Test",
        second_name=f"P{player_id}",
        web_name=f"P{player_id}",
        team_id=team_id,
        position=Position.MID,
        now_cost=now_cost,
        status=InjuryStatus.AVAILABLE,
        is_active=True,
    )
    db.add(player)
    db.flush()
    return player


def _pick(element: int, position: int, is_captain: bool = False, is_vice_captain: bool = False) -> dict:
    return {
        "element": element,
        "position": position,
        "multiplier": 2 if is_captain else 1,
        "is_captain": is_captain,
        "is_vice_captain": is_vice_captain,
    }


def _picks_payload(
    picks: list[dict], bank: int = 5, value: int = 1000, active_chip: str | None = None
) -> dict:
    return {
        "active_chip": active_chip,
        "entry_history": {
            "event": 1,
            "bank": bank,
            "value": value,
            "overall_rank": 123456,
            "total_points": 60,
        },
        "picks": picks,
    }


def _seed_15_players(db, team_id: int) -> list[int]:
    ids = list(range(1, 16))
    for pid in ids:
        _player(db, pid, team_id)
    return ids


class TestMapSquadPlayers:
    def test_starting_xi_and_bench_split(self, db_session) -> None:
        team = _team(db_session, 1, "Arsenal")
        ids = _seed_15_players(db_session, team.id)
        picks = [_pick(pid, position) for pid, position in zip(ids, range(1, 16))]

        service = SquadSyncService()
        result = service.sync_from_fpl_payloads(
            db_session, gameweek=1, picks_payload=_picks_payload(picks)
        )
        db_session.commit()

        state = db_session.query(SquadState).filter_by(gameweek=1).one()
        starting = [sp for sp in state.players if sp.is_starting]
        bench = [sp for sp in state.players if not sp.is_starting]
        assert len(starting) == 11
        assert len(bench) == 4
        assert result.players_created == 15


class TestFirstSync:
    def test_first_ever_sync_gives_one_free_transfer(self, db_session) -> None:
        team = _team(db_session, 1, "Arsenal")
        ids = _seed_15_players(db_session, team.id)
        picks = [_pick(pid, position) for pid, position in zip(ids, range(1, 16))]

        service = SquadSyncService()
        result = service.sync_from_fpl_payloads(
            db_session, gameweek=1, picks_payload=_picks_payload(picks)
        )
        db_session.commit()

        assert result.free_transfers == 1
        assert result.state_created is True
        assert set(result.chips_available) == {
            "wildcard",
            "free_hit",
            "bench_boost",
            "triple_captain",
        }

    def test_captain_and_vice_captain_flags_are_set(self, db_session) -> None:
        team = _team(db_session, 1, "Arsenal")
        ids = _seed_15_players(db_session, team.id)
        picks = [
            _pick(pid, position, is_captain=(pid == 1), is_vice_captain=(pid == 2))
            for pid, position in zip(ids, range(1, 16))
        ]

        service = SquadSyncService()
        service.sync_from_fpl_payloads(db_session, gameweek=1, picks_payload=_picks_payload(picks))
        db_session.commit()

        state = db_session.query(SquadState).filter_by(gameweek=1).one()
        captain = next(sp for sp in state.players if sp.player_id == 1)
        vice = next(sp for sp in state.players if sp.player_id == 2)
        assert captain.is_captain is True
        assert vice.is_vice_captain is True


class TestRepeatedSyncAndTransfers:
    def _sync_gw1(self, db_session, team_id: int) -> list[int]:
        ids = _seed_15_players(db_session, team_id)
        picks = [_pick(pid, position) for pid, position in zip(ids, range(1, 16))]
        SquadSyncService().sync_from_fpl_payloads(
            db_session, gameweek=1, picks_payload=_picks_payload(picks)
        )
        db_session.commit()
        return ids

    def test_no_transfers_banks_a_free_transfer(self, db_session) -> None:
        team = _team(db_session, 1, "Arsenal")
        ids = self._sync_gw1(db_session, team.id)

        picks_gw2 = [_pick(pid, position) for pid, position in zip(ids, range(1, 16))]
        result = SquadSyncService().sync_from_fpl_payloads(
            db_session, gameweek=2, picks_payload=_picks_payload(picks_gw2)
        )
        db_session.commit()

        # No TransferHistory rows for gw1 -> unused FT banks: 1 - 0 + 1 = 2.
        assert result.free_transfers == 2

    def test_one_transfer_used_keeps_free_transfers_at_one(self, db_session) -> None:
        team = _team(db_session, 1, "Arsenal")
        ids = self._sync_gw1(db_session, team.id)

        db_session.add(
            TransferHistory(gameweek=1, player_out_id=ids[0], player_in_id=ids[0] + 100)
        )
        db_session.commit()

        picks_gw2 = [_pick(pid, position) for pid, position in zip(ids, range(1, 16))]
        result = SquadSyncService().sync_from_fpl_payloads(
            db_session, gameweek=2, picks_payload=_picks_payload(picks_gw2)
        )
        db_session.commit()

        # 1 (previous FT) - 1 (used) + 1 (new week's grant) = 1.
        assert result.free_transfers == 1

    def test_resyncing_same_gameweek_removes_dropped_players(self, db_session) -> None:
        """`players_removed` only applies within one gameweek's row: a
        `SquadState` is a per-gameweek snapshot, so a re-sync of the SAME
        gameweek (e.g. picks corrected, or polled again before/after a
        late change) should drop anyone no longer in the payload. A
        transfer *between* two different gameweeks naturally lands in two
        separate rows and isn't a "removal" in this sense.
        """
        team = _team(db_session, 1, "Arsenal")
        ids = self._sync_gw1(db_session, team.id)

        picks_gw2_v1 = [_pick(pid, position) for pid, position in zip(ids, range(1, 16))]
        SquadSyncService().sync_from_fpl_payloads(
            db_session, gameweek=2, picks_payload=_picks_payload(picks_gw2_v1)
        )
        db_session.commit()

        # Re-sync gw2: player id 1 is now replaced by a new player id 100.
        remaining = ids[1:]
        _player(db_session, 100, team.id)
        picks_gw2_v2 = [_pick(pid, i + 1) for i, pid in enumerate(remaining + [100])]

        result = SquadSyncService().sync_from_fpl_payloads(
            db_session, gameweek=2, picks_payload=_picks_payload(picks_gw2_v2)
        )
        db_session.commit()

        state = db_session.query(SquadState).filter_by(gameweek=2).one()
        player_ids = {sp.player_id for sp in state.players}
        assert 1 not in player_ids
        assert 100 in player_ids
        assert result.players_removed == 1
        assert result.players_created == 1
        assert result.state_created is False

    def test_wildcard_gameweek_does_not_consume_free_transfer(self, db_session) -> None:
        team = _team(db_session, 1, "Arsenal")
        ids = self._sync_gw1(db_session, team.id)

        # gw2: no transfers yet, so its free-transfer count is gw1's
        # unused FT banked: 1 - 0 + 1 = 2.
        picks_gw2 = [_pick(pid, position) for pid, position in zip(ids, range(1, 16))]
        gw2_result = SquadSyncService().sync_from_fpl_payloads(
            db_session, gameweek=2, picks_payload=_picks_payload(picks_gw2, active_chip="wildcard")
        )
        db_session.commit()
        assert gw2_result.free_transfers == 2

        # Play wildcard *in* gw2 and log several "transfers" under it -
        # irrelevant to gw3's count since a wildcard gameweek is
        # transfer-neutral (doesn't consume any of gw2's free transfers).
        for i in range(1, 6):
            db_session.add(
                TransferHistory(gameweek=2, player_out_id=ids[i], player_in_id=ids[i] + 200)
            )
        db_session.commit()

        picks_gw3 = [_pick(pid, position) for pid, position in zip(ids, range(1, 16))]
        result = SquadSyncService().sync_from_fpl_payloads(
            db_session, gameweek=3, picks_payload=_picks_payload(picks_gw3)
        )
        db_session.commit()

        # gw2 had 2 FTs and was a wildcard gameweek, so none are spent:
        # 2 (gw2's FT) - 0 (wildcard-neutral) + 1 = 3.
        assert result.free_transfers == 3
        # Wildcard used in gw2 should no longer be in the available list.
        assert "wildcard" not in result.chips_available


class TestComputeHelpers:
    def test_compute_free_transfers_floors_at_one(self, db_session) -> None:
        team = _team(db_session, 1, "Arsenal")
        _seed_15_players(db_session, team.id)
        state = SquadState(gameweek=1, bank_balance=0, squad_value=1000, free_transfers=1)
        db_session.add(state)
        db_session.commit()

        # Using more transfers than available should never go negative.
        assert compute_free_transfers(state, transfers_made_previous_gw=5, chip_played_previous_gw=None) == 1

    def test_compute_chips_available_excludes_used(self) -> None:
        available = compute_chips_available(["wildcard", None, "bench_boost"], active_chip_this_gw=None)
        assert set(available) == {"free_hit", "triple_captain"}
