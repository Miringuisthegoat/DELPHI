"""
Phase 6: `TransferOptimizerService` - DELPHI's transfer recommendation engine.

Reads the user's current squad (`SquadState`/`SquadPlayer`, Phase 2) and
DELPHI's stored `Prediction` rows (Phase 5), then uses Google OR-Tools'
CP-SAT solver to find the highest-value transfer move for the requested
horizon (1, 3, or 5 gameweeks), respecting every official FPL squad rule.

Design notes
------------
* **Only the delta matters.** A squad's size (15) and per-position quota
  (2 GKP / 5 DEF / 5 MID / 3 FWD) never change across a transfer, so every
  untouched player contributes the same predicted points whether or not a
  transfer happens - it cancels out of the comparison. The objective is
  therefore just: sum(predicted points of players bought in) - sum
  (predicted points of players sold) - point-hit cost. This keeps the
  CP-SAT model small even with ~500+ candidate players in the database.
* **One solve per transfer count.** Rather than one model that also
  decides "how many transfers", `optimize()` solves separately for
  0..`max_transfers` transfers and compares the resulting net expected
  gain. This directly matches the project brief's "evaluate no transfer /
  one transfer / two transfers, including hits" requirement, and it means
  every alternative (not just the winner) is available for the weekly
  report.
* **Squad-shape preserving by construction.** The solver requires the
  number of players sold at each position to equal the number bought at
  that position, and requires each club's post-transfer count to stay at
  or under 3 - so any solution it returns is automatically a legal 15-man
  squad, never something that needs a second validation pass.
* **Candidate pool is pruned for tractability.** Buy-candidates are
  narrowed to players priced within what a single sale could plausibly
  fund, then capped to the top `candidate_pool_size` per position by
  predicted points - comparing every owned player against all ~500
  alternatives in the database would be wasted solver time for
  candidates that could never be the optimal swap.
* **Single-threaded solver search.** `num_search_workers` is pinned to 1
  because OR-Tools' multi-threaded CP-SAT search has been observed to
  crash (native access violation) on some Windows + recent-CPython
  combinations. A single worker is slightly slower in theory but well
  within `_SOLVER_TIME_LIMIT_SECONDS` for a 15-player squad's worth of
  candidates, and it's the standard workaround for this failure mode.
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import OptimizationError
from enums import Position
from app.models.player import Player
from app.models.prediction import Prediction
from app.models.squad import SquadState
from app.optimization.models import OptimizationResult, PlayerMove, TransferOption

_POINTS_SCALE = 100
"""Predicted points are floats; CP-SAT wants integers, so scale by 100
(i.e. work in hundredths of a point) before rounding."""

_MAX_PLAYERS_PER_CLUB = 3
_SOLVER_TIME_LIMIT_SECONDS = 8.0


@dataclass
class _SquadMember:
    player_id: int
    web_name: str
    position: Position
    team_id: int
    selling_price: int
    predicted_points: float


@dataclass
class _Candidate:
    player_id: int
    web_name: str
    position: Position
    team_id: int
    now_cost: int
    predicted_points: float


class TransferOptimizerService:
    """Finds the highest-value transfer move for the user's current squad."""

    def optimize(
        self,
        db: Session,
        gameweek: int,
        horizon: int = 1,
        max_transfers: int = 2,
        candidate_pool_size: int = 40,
    ) -> OptimizationResult:
        """Evaluate 0..`max_transfers` transfers and recommend the best.

        Args:
            db: Active SQLAlchemy session.
            gameweek: The upcoming gameweek being planned for. The most
                recent `SquadState` at or before this gameweek is used as
                the current squad.
            horizon: Which stored `Prediction.horizon` to optimize
                against (1, 3, or 5 - matching `settings.ml_default_horizons`).
                Optimizing against a longer horizon favours players whose
                *run* of fixtures is good, not just the next one.
            max_transfers: Highest transfer count to evaluate (0..this,
                inclusive). 2 covers the vast majority of realistic weekly
                decisions without exploding solver time.
            candidate_pool_size: How many top-predicted-points candidates
                per position to feed the solver (see module docstring).
        """
        squad_state = self._load_squad_state(db, gameweek)
        if not squad_state.players:
            raise OptimizationError(
                f"Squad state for gameweek {squad_state.gameweek} has no players "
                "recorded - sync 'my squad' before requesting an optimization."
            )

        owned_ids = [sp.player_id for sp in squad_state.players]
        players_by_id = {
            p.id: p
            for p in db.execute(select(Player).where(Player.id.in_(owned_ids))).scalars().all()
        }
        missing = [pid for pid in owned_ids if pid not in players_by_id]
        if missing:
            raise OptimizationError(
                f"Squad references player id(s) {missing} not found in the players table."
            )

        predictions = self._load_predictions(db, gameweek, horizon, owned_ids)

        squad = [
            _SquadMember(
                player_id=sp.player_id,
                web_name=players_by_id[sp.player_id].web_name,
                position=players_by_id[sp.player_id].position,
                team_id=players_by_id[sp.player_id].team_id,
                selling_price=sp.selling_price,
                predicted_points=predictions.get(sp.player_id, 0.0),
            )
            for sp in squad_state.players
        ]

        current_club_counts: dict[int, int] = {}
        for member in squad:
            current_club_counts[member.team_id] = current_club_counts.get(member.team_id, 0) + 1

        candidates = self._build_candidate_pool(
            db, gameweek, horizon, owned_ids, squad_state.bank_balance, squad, candidate_pool_size
        )

        options: list[TransferOption] = []
        for transfer_count in range(0, max_transfers + 1):
            option = self._solve_for_transfer_count(
                squad, candidates, current_club_counts, squad_state.bank_balance, transfer_count
            )
            if option.feasible:
                option.hit_cost = max(0, transfer_count - squad_state.free_transfers) * 4
                option.net_expected_gain = round(
                    option.points_gained - option.points_lost - option.hit_cost, 2
                )
                option.reasoning = self._reasoning(option)
            options.append(option)

        feasible_options = [o for o in options if o.feasible]
        if not feasible_options:
            raise OptimizationError(
                "No feasible transfer option - including making no transfer at all - "
                "could be evaluated. Check that squad/prediction data is loaded."
            )
        recommended = max(feasible_options, key=lambda o: o.net_expected_gain)

        return OptimizationResult(
            gameweek=gameweek,
            horizon=horizon,
            free_transfers=squad_state.free_transfers,
            options=options,
            recommended=recommended,
        )

    # --- Data loading -----------------------------------------------------

    @staticmethod
    def _load_squad_state(db: Session, gameweek: int) -> SquadState:
        state = (
            db.execute(
                select(SquadState)
                .where(SquadState.gameweek <= gameweek)
                .order_by(SquadState.gameweek.desc())
            )
            .scalars()
            .first()
        )
        if state is None:
            raise OptimizationError(
                f"No squad state found at or before gameweek {gameweek}. "
                "Sync 'my squad' before requesting an optimization."
            )
        return state

    @staticmethod
    def _load_predictions(
        db: Session, gameweek: int, horizon: int, player_ids: list[int]
    ) -> dict[int, float]:
        rows = db.execute(
            select(Prediction.player_id, Prediction.predicted_points).where(
                Prediction.gameweek == gameweek,
                Prediction.horizon == horizon,
                Prediction.player_id.in_(player_ids),
            )
        ).all()
        return {player_id: float(points) for player_id, points in rows}

    def _build_candidate_pool(
        self,
        db: Session,
        gameweek: int,
        horizon: int,
        owned_ids: list[int],
        bank_balance: int,
        squad: list[_SquadMember],
        pool_size: int,
    ) -> list[_Candidate]:
        """Active, unowned players with a prediction for this gameweek/horizon.

        Pre-filters by an affordability ceiling (bank + the squad's single
        most valuable sellable player) as a coarse prune - the solver's own
        budget constraint remains the source of truth for whether a
        specific combination is actually affordable.
        """
        max_sell = max((m.selling_price for m in squad), default=0)
        affordability_ceiling = bank_balance + max_sell

        rows = db.execute(
            select(Player, Prediction.predicted_points)
            .join(Prediction, Prediction.player_id == Player.id)
            .where(
                Player.is_active.is_(True),
                Player.id.notin_(owned_ids),
                Prediction.gameweek == gameweek,
                Prediction.horizon == horizon,
                Player.now_cost <= affordability_ceiling,
            )
        ).all()

        by_position: dict[Position, list[_Candidate]] = {}
        for player, predicted_points in rows:
            candidate = _Candidate(
                player_id=player.id,
                web_name=player.web_name,
                position=player.position,
                team_id=player.team_id,
                now_cost=player.now_cost,
                predicted_points=float(predicted_points),
            )
            by_position.setdefault(player.position, []).append(candidate)

        pooled: list[_Candidate] = []
        for position_candidates in by_position.values():
            position_candidates.sort(key=lambda c: c.predicted_points, reverse=True)
            pooled.extend(position_candidates[:pool_size])
        return pooled

    # --- Solving ------------------------------------------------------------

    def _solve_for_transfer_count(
        self,
        squad: list[_SquadMember],
        candidates: list[_Candidate],
        current_club_counts: dict[int, int],
        bank_balance: int,
        transfer_count: int,
    ) -> TransferOption:
        if transfer_count == 0:
            return TransferOption(
                transfers=0,
                players_out=[],
                players_in=[],
                points_gained=0.0,
                points_lost=0.0,
                hit_cost=0,
                net_expected_gain=0.0,
                bank_after=round(bank_balance / 10, 1),
                feasible=True,
                reasoning="",
            )

        model = cp_model.CpModel()
        sell_vars = {m.player_id: model.NewBoolVar(f"sell_{m.player_id}") for m in squad}
        buy_vars = {c.player_id: model.NewBoolVar(f"buy_{c.player_id}") for c in candidates}

        if not candidates:
            return self._infeasible_option(transfer_count, bank_balance)

        model.Add(sum(sell_vars.values()) == transfer_count)
        model.Add(sum(buy_vars.values()) == transfer_count)

        # Selling/buying counts must match per position, so the squad's
        # 2/5/5/3 shape is preserved automatically.
        for position in Position:
            sold_in_position = sum(
                sell_vars[m.player_id] for m in squad if m.position == position
            )
            bought_in_position = sum(
                buy_vars[c.player_id] for c in candidates if c.position == position
            )
            model.Add(sold_in_position == bought_in_position)

        # No more than 3 players from any one club after the transfer.
        for club_id, current_count in current_club_counts.items():
            sold_from_club = sum(
                sell_vars[m.player_id] for m in squad if m.team_id == club_id
            )
            bought_from_club = sum(
                buy_vars[c.player_id] for c in candidates if c.team_id == club_id
            )
            model.Add(current_count - sold_from_club + bought_from_club <= _MAX_PLAYERS_PER_CLUB)

        freed_budget = sum(m.selling_price * sell_vars[m.player_id] for m in squad)
        spend = sum(c.now_cost * buy_vars[c.player_id] for c in candidates)
        model.Add(freed_budget + bank_balance >= spend)

        objective_terms = [
            int(round(c.predicted_points * _POINTS_SCALE)) * buy_vars[c.player_id]
            for c in candidates
        ] + [
            -int(round(m.predicted_points * _POINTS_SCALE)) * sell_vars[m.player_id]
            for m in squad
        ]
        model.Maximize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = _SOLVER_TIME_LIMIT_SECONDS
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return self._infeasible_option(transfer_count, bank_balance)

        players_out = [
            self._to_move(m) for m in squad if solver.Value(sell_vars[m.player_id])
        ]
        players_in = [
            self._to_move(c) for c in candidates if solver.Value(buy_vars[c.player_id])
        ]

        freed = sum(m.selling_price for m in squad if solver.Value(sell_vars[m.player_id]))
        spent = sum(c.now_cost for c in candidates if solver.Value(buy_vars[c.player_id]))

        return TransferOption(
            transfers=transfer_count,
            players_out=players_out,
            players_in=players_in,
            points_gained=round(sum(p.predicted_points for p in players_in), 2),
            points_lost=round(sum(p.predicted_points for p in players_out), 2),
            hit_cost=0,  # filled in by the caller, which knows free_transfers
            net_expected_gain=0.0,
            bank_after=round((bank_balance + freed - spent) / 10, 1),
            feasible=True,
            reasoning="",
        )

    @staticmethod
    def _infeasible_option(transfer_count: int, bank_balance: int) -> TransferOption:
        return TransferOption(
            transfers=transfer_count,
            players_out=[],
            players_in=[],
            points_gained=0.0,
            points_lost=0.0,
            hit_cost=0,
            net_expected_gain=float("-inf"),
            bank_after=round(bank_balance / 10, 1),
            feasible=False,
            reasoning=(
                f"No valid {transfer_count}-transfer combination respects the budget, "
                "club limit, and position-quota rules with the currently available "
                "candidate players."
            ),
        )

    @staticmethod
    def _to_move(member: _SquadMember | _Candidate) -> PlayerMove:
        price = member.selling_price if isinstance(member, _SquadMember) else member.now_cost
        return PlayerMove(
            player_id=member.player_id,
            web_name=member.web_name,
            position=member.position.value,
            price_millions=round(price / 10, 1),
            predicted_points=round(member.predicted_points, 2),
        )

    @staticmethod
    def _reasoning(option: TransferOption) -> str:
        if option.transfers == 0:
            return "Holding the squad scores as well as (or better than) any affordable swap this week."

        names_out = ", ".join(p.web_name for p in option.players_out)
        names_in = ", ".join(p.web_name for p in option.players_in)
        hit_note = f" (a -{option.hit_cost} point hit)" if option.hit_cost else ""

        return (
            f"{names_out} -> {names_in}{hit_note}: projects +{option.points_gained:.1f} pts "
            f"in vs -{option.points_lost:.1f} pts out over the horizon, netting "
            f"{option.net_expected_gain:+.1f} points."
        )