"""Phase 10: profile `TransferOptimizerService` against a realistically-sized
player pool (~600 players, like a real Premier League season), across
different `candidate_pool_size` values, to check solver timing stays well
within `_SOLVER_TIME_LIMIT_SECONDS` (8s) before relying on this for a
real season.

Runs entirely against a throwaway in-memory SQLite database with
synthetic data - never touches the real project database or the FPL API.

Usage:
    python -m scripts.profile_optimizer
    python -m scripts.profile_optimizer --players 700 --pool-sizes 20 40 80
"""

from __future__ import annotations

import argparse
import random
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import configure_logging
from app.db.base import Base
from app.models.enums import InjuryStatus, Position
from app.models.player import Player
from app.models.prediction import Prediction
from app.models.squad import SquadPlayer, SquadState
from app.models.team import Team
from app.optimization.transfer_optimizer import TransferOptimizerService

import app.models  # noqa: F401  (registers every model on Base.metadata)

_GAMEWEEK = 10
_HORIZON = 1
_N_CLUBS = 20
_SQUAD_SHAPE = {Position.GKP: 2, Position.DEF: 5, Position.MID: 5, Position.FWD: 3}
_POOL_SHAPE = {Position.GKP: 60, Position.DEF: 180, Position.MID: 220, Position.FWD: 140}
"""Rough real-world FPL split (~20 clubs x ~15 outfield-relevant players
+ backup keepers) totalling ~600 by default."""


def _build_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _seed_world(db: Session, n_players: int, seed: int) -> SquadState:
    rng = random.Random(seed)

    for club_id in range(1, _N_CLUBS + 1):
        db.add(Team(id=club_id, name=f"Club{club_id}", short_name=f"C{club_id:02d}"))
    db.flush()

    # Scale the position pool proportionally if --players overrides the default total.
    default_total = sum(_POOL_SHAPE.values())
    scale = n_players / default_total
    pool_shape = {pos: max(1, round(count * scale)) for pos, count in _POOL_SHAPE.items()}

    player_id = 1
    players_by_position: dict[Position, list[int]] = {pos: [] for pos in Position}
    for position, count in pool_shape.items():
        for _ in range(count):
            club_id = rng.randint(1, _N_CLUBS)
            now_cost = rng.randint(40, 140)
            db.add(
                Player(
                    id=player_id,
                    first_name="Synthetic",
                    second_name=f"Player{player_id}",
                    web_name=f"P{player_id}",
                    team_id=club_id,
                    position=position,
                    now_cost=now_cost,
                    status=InjuryStatus.AVAILABLE,
                    is_active=True,
                )
            )
            db.add(
                Prediction(
                    player_id=player_id,
                    gameweek=_GAMEWEEK,
                    horizon=_HORIZON,
                    predicted_points=round(rng.uniform(0.5, 12.0), 2),
                    confidence=0.7,
                )
            )
            players_by_position[position].append(player_id)
            player_id += 1
    db.flush()

    # Build a legal starting squad: 2/5/5/3, <=3 per club, from the pool
    # itself (so squad members are also valid Prediction/Player rows).
    club_counts: dict[int, int] = {}
    squad_ids: list[int] = []
    for position, quota in _SQUAD_SHAPE.items():
        candidates = list(players_by_position[position])
        rng.shuffle(candidates)
        picked = 0
        for pid in candidates:
            if picked >= quota:
                break
            player = db.get(Player, pid)
            if club_counts.get(player.team_id, 0) >= 3:
                continue
            squad_ids.append(pid)
            club_counts[player.team_id] = club_counts.get(player.team_id, 0) + 1
            picked += 1

    state = SquadState(
        gameweek=_GAMEWEEK, bank_balance=50, squad_value=900, free_transfers=2
    )
    db.add(state)
    db.flush()
    for pid in squad_ids:
        player = db.get(Player, pid)
        state.players.append(
            SquadPlayer(
                player_id=pid,
                purchase_price=player.now_cost,
                selling_price=player.now_cost,
                is_starting=True,
            )
        )
    db.flush()
    return state


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile the transfer optimizer against a synthetic full-size player pool."
    )
    parser.add_argument(
        "--players", type=int, default=sum(_POOL_SHAPE.values()), help="Total synthetic players to generate."
    )
    parser.add_argument(
        "--pool-sizes",
        type=int,
        nargs="*",
        default=[20, 40, 80],
        help="candidate_pool_size values to benchmark.",
    )
    parser.add_argument(
        "--max-transfers", type=int, default=2, help="Highest transfer count to evaluate per run."
    )
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    configure_logging()

    print(f"Seeding {args.players} synthetic players across {_N_CLUBS} clubs...")
    db = _build_session()
    _seed_world(db, n_players=args.players, seed=args.seed)

    optimizer = TransferOptimizerService()

    print(
        f"{'pool_size':>10} | {'seconds':>8} | {'recommended':>11} | {'net_gain':>9}"
    )
    print("-" * 48)
    for pool_size in args.pool_sizes:
        started = time.perf_counter()
        result = optimizer.optimize(
            db,
            gameweek=_GAMEWEEK,
            horizon=_HORIZON,
            max_transfers=args.max_transfers,
            candidate_pool_size=pool_size,
        )
        elapsed = time.perf_counter() - started
        print(
            f"{pool_size:>10} | {elapsed:>8.2f} | "
            f"{result.recommended.transfers:>11} | {result.recommended.net_expected_gain:>+9.2f}"
        )

    db.close()


if __name__ == "__main__":
    main()
