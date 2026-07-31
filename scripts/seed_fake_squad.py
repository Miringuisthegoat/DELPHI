"""One-off / dev-only script to seed a FAKE squad for dashboard testing.

Unlike `scripts.sync_squad`, this does NOT call the FPL API - it builds a
valid 15-man squad by picking real, already-synced players straight out of
your local `players` table (whatever `sync_data`/`sync_full` has already
pulled in), respecting the same shape rules the rest of the app enforces:

    2 GKP / 5 DEF / 5 MID / 3 FWD, no more than 3 players from one club.

This exists purely so the Phase 8 dashboard has a `SquadState` to render
while you're developing against it - it is NOT a substitute for
`scripts.sync_squad`, which pulls your *real* FPL team. Re-running this
script for the same --gameweek overwrites that gameweek's fake squad
(upsert, same convention as every other sync in this project).

Usage:
    python -m scripts.seed_fake_squad --gameweek 1
    python -m scripts.seed_fake_squad --gameweek 1 --budget 1000
"""

from __future__ import annotations

import argparse
import random

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.models.enums import Position
from app.models.player import Player
from app.models.squad import SquadPlayer, SquadState

_SHAPE: dict[Position, int] = {
    Position.GKP: 2,
    Position.DEF: 5,
    Position.MID: 5,
    Position.FWD: 3,
}
_MAX_PER_CLUB = 3


def _pick_players(db: Session, budget: int, seed: int) -> list[Player]:
    """Greedily pick a valid, roughly-affordable 15-man squad from real players.

    Not an optimizer - just a random-but-constrained pick (cheapest-first
    bias) so the fake squad is plausible and always respects the club/
    position rules the rest of the app assumes are true.
    """
    rng = random.Random(seed)
    squad: list[Player] = []
    club_counts: dict[int, int] = {}
    remaining_budget = budget

    for position, quota in _SHAPE.items():
        candidates = (
            db.execute(
                select(Player)
                .where(Player.position == position, Player.is_active.is_(True))
                .order_by(Player.now_cost.asc())
            )
            .scalars()
            .all()
        )
        rng.shuffle(candidates)
        # Bias toward cheaper players first so the budget stretches across
        # all 15 slots, but keep some randomness so it's not the same
        # 2 cheapest keepers every run.
        candidates.sort(key=lambda p: p.now_cost + rng.random() * 15)

        picked = 0
        for player in candidates:
            if picked >= quota:
                break
            if club_counts.get(player.team_id, 0) >= _MAX_PER_CLUB:
                continue
            if player.now_cost > remaining_budget:
                continue
            squad.append(player)
            club_counts[player.team_id] = club_counts.get(player.team_id, 0) + 1
            remaining_budget -= player.now_cost
            picked += 1

        if picked < quota:
            raise SystemExit(
                f"Could not find {quota} affordable {position.value} players "
                f"within budget/club constraints - try a larger --budget, or "
                f"run a full sync first (python -m scripts.sync_data)."
            )

    return squad


def seed_fake_squad(
    db: Session, gameweek: int, budget: int = 1000, seed: int = 42
) -> SquadState:
    """Build and upsert a fake `SquadState` (+ 15 `SquadPlayer` rows) for `gameweek`."""
    squad_players = _pick_players(db, budget=budget, seed=seed)
    spent = sum(p.now_cost for p in squad_players)
    bank_balance = max(budget - spent, 0)

    state = db.query(SquadState).filter_by(gameweek=gameweek).one_or_none()
    if state is None:
        state = SquadState(gameweek=gameweek)
        db.add(state)
    else:
        # Clear existing fake picks before re-seeding this gameweek.
        for sp in list(state.players):
            db.delete(sp)
        db.flush()

    state.bank_balance = bank_balance
    state.squad_value = spent
    state.free_transfers = 1
    state.chips_available = ["wildcard", "free_hit", "bench_boost", "triple_captain"]
    state.chip_played = None
    state.overall_rank = None
    state.total_points = 0
    db.flush()

    # Starting XI: cheapest-valid formation isn't the point here, just
    # something legal-looking - 1 GKP, 4 DEF, 4 MID, 2 FWD starting,
    # remainder benched. Captain/vice go to the two priciest outfielders.
    by_position: dict[Position, list[Player]] = {}
    for player in squad_players:
        by_position.setdefault(player.position, []).append(player)

    starting_ids: set[int] = set()
    starting_ids.update(p.id for p in by_position.get(Position.GKP, [])[:1])
    starting_ids.update(p.id for p in by_position.get(Position.DEF, [])[:4])
    starting_ids.update(p.id for p in by_position.get(Position.MID, [])[:4])
    starting_ids.update(p.id for p in by_position.get(Position.FWD, [])[:2])

    bench_order = 1
    outfield_by_price = sorted(
        (p for p in squad_players if p.position != Position.GKP),
        key=lambda p: p.now_cost,
        reverse=True,
    )
    captain_id = outfield_by_price[0].id if outfield_by_price else None
    vice_id = outfield_by_price[1].id if len(outfield_by_price) > 1 else None

    for player in squad_players:
        is_starting = player.id in starting_ids
        db.add(
            SquadPlayer(
                squad_state_id=state.id,
                player_id=player.id,
                purchase_price=player.now_cost,
                selling_price=player.now_cost,
                is_starting=is_starting,
                bench_position=None if is_starting else bench_order,
                is_captain=(player.id == captain_id),
                is_vice_captain=(player.id == vice_id),
            )
        )
        if not is_starting:
            bench_order += 1

    db.flush()
    logger.info(
        "Seeded fake squad for gw {}: {} players, bank={:.1f}m, squad_value={:.1f}m",
        gameweek,
        len(squad_players),
        bank_balance / 10,
        spent / 10,
    )
    return state


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed a fake squad (real players, random selection) for dashboard testing."
    )
    parser.add_argument("--gameweek", type=int, required=True, help="Gameweek to seed.")
    parser.add_argument(
        "--budget", type=int, default=1000, help="Total budget in tenths of a million (default 1000 = £100.0m)."
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed, for reproducible picks.")
    args = parser.parse_args()

    configure_logging()
    init_db()

    with session_scope() as db:
        seed_fake_squad(db, gameweek=args.gameweek, budget=args.budget, seed=args.seed)

    logger.info(
        "Done. View it at http://127.0.0.1:8000/dashboard?gameweek={}",
        args.gameweek,
    )


if __name__ == "__main__":
    main()