"""Tests for `app.optimization.transfer_optimizer.TransferOptimizerService`.

The current squad is deliberately spread across 5 clubs (3 players each)
so the <=3-players-per-club rule is satisfied by the starting state and
doesn't spuriously block every transfer - any new candidate is given its
own, previously-unused club id for the same reason.
"""

from __future__ import annotations

from app.models.enums import InjuryStatus, Position
from app.models.player import Player
from app.models.prediction import Prediction
from app.models.squad import SquadPlayer, SquadState
from app.models.team import Team
from app.optimization.transfer_optimizer import TransferOptimizerService

_GAMEWEEK = 5
_HORIZON = 1


def _team(db, team_id: int, name: str) -> Team:
    team = Team(id=team_id, name=name, short_name=name[:3].upper())
    db.add(team)
    db.flush()
    return team


def _player(db, player_id: int, team_id: int, position: Position, now_cost: int) -> Player:
    player = Player(
        id=player_id,
        first_name="Test",
        second_name=f"P{player_id}",
        web_name=f"P{player_id}",
        team_id=team_id,
        position=position,
        now_cost=now_cost,
        status=InjuryStatus.AVAILABLE,
        is_active=True,
    )
    db.add(player)
    db.flush()
    return player


def _prediction(db, player_id: int, points: float) -> None:
    db.add(
        Prediction(
            player_id=player_id,
            gameweek=_GAMEWEEK,
            horizon=_HORIZON,
            predicted_points=points,
            confidence=0.7,
        )
    )


def _minimal_squad(db, free_transfers: int = 1, bank: int = 0) -> SquadState:
    """2 GKP / 5 DEF / 5 MID / 3 FWD, spread 3-per-club across 5 clubs."""
    positions = [Position.GKP] * 2 + [Position.DEF] * 5 + [Position.MID] * 5 + [Position.FWD] * 3
    for team_id in range(1, 6):
        _team(db, team_id, f"Team{team_id}")

    state = SquadState(
        gameweek=_GAMEWEEK, bank_balance=bank, squad_value=15 * 50, free_transfers=free_transfers
    )
    db.add(state)
    db.flush()

    for i, position in enumerate(positions, start=1):
        team_id = ((i - 1) // 3) + 1
        _player(db, i, team_id, position, now_cost=50)
        state.players.append(
            SquadPlayer(player_id=i, purchase_price=50, selling_price=50, is_starting=True)
        )
    db.flush()
    return state


def test_no_upgrade_available_recommends_zero_transfers(db_session):
    _minimal_squad(db_session)
    for i in range(1, 16):
        _prediction(db_session, i, points=4.0)
    db_session.flush()

    result = TransferOptimizerService().optimize(
        db_session, gameweek=_GAMEWEEK, horizon=_HORIZON, max_transfers=1
    )

    assert result.recommended.transfers == 0


def test_clear_upgrade_is_recommended(db_session):
    _minimal_squad(db_session, bank=50)
    for i in range(1, 16):
        _prediction(db_session, i, points=4.0)

    star_team = _team(db_session, 6, "StarFC")
    _player(db_session, 100, star_team.id, Position.FWD, now_cost=90)
    _prediction(db_session, 100, points=12.0)
    db_session.flush()

    result = TransferOptimizerService().optimize(
        db_session, gameweek=_GAMEWEEK, horizon=_HORIZON, max_transfers=1
    )

    assert result.recommended.transfers == 1
    assert 100 in [p.player_id for p in result.recommended.players_in]


def test_second_transfer_rejected_when_hit_outweighs_gain(db_session):
    _minimal_squad(db_session, bank=100, free_transfers=1)
    for i in range(1, 16):
        _prediction(db_session, i, points=4.0)

    star_team = _team(db_session, 6, "StarFC")
    _player(db_session, 100, star_team.id, Position.FWD, now_cost=90)
    _prediction(db_session, 100, points=8.0)  # solid, but not spectacular

    marginal_team = _team(db_session, 7, "MarginalFC")
    _player(db_session, 101, marginal_team.id, Position.FWD, now_cost=55)
    _prediction(db_session, 101, points=4.5)  # barely better than what's owned
    db_session.flush()

    result = TransferOptimizerService().optimize(
        db_session, gameweek=_GAMEWEEK, horizon=_HORIZON, max_transfers=2
    )

    # The 2nd transfer's -4 hit outweighs a <1pt gain, so 1 transfer wins.
    assert result.recommended.transfers <= 1


def test_budget_constraint_blocks_unaffordable_transfer(db_session):
    _minimal_squad(db_session, bank=0)
    for i in range(1, 16):
        _prediction(db_session, i, points=4.0)

    star_team = _team(db_session, 6, "StarFC")
    _player(db_session, 100, star_team.id, Position.FWD, now_cost=200)
    _prediction(db_session, 100, points=20.0)
    db_session.flush()

    result = TransferOptimizerService().optimize(
        db_session, gameweek=_GAMEWEEK, horizon=_HORIZON, max_transfers=1
    )

    bought_ids = [p.player_id for p in result.recommended.players_in]
    assert 100 not in bought_ids
