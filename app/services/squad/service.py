"""
Phase 7: `SquadSyncService` - persists "My Squad" into `SquadState`/`SquadPlayer`.

Takes the raw dict payloads Phase 3's `FPLAPIClient.get_entry()` /
`get_entry_event_picks()` already fetch (typed as dicts there, since a
manager's own team is a per-user rather than global-schema concern - see
that client's docstring) and turns them into the same upsert-by-natural-key
rows every other sync service in this project writes (Phase 4's
`DataIngestionService`, Phase 5's `DelphiPredictionEngine`).

This is what makes the Phase 6 transfer optimizer usable: it reads
`SquadState`/`SquadPlayer` to know what you currently own, and until this
service has run at least once for the current season, `TransferOptimizerService.optimize()`
raises `OptimizationError` (see its `_load_squad_state`).
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session

from app.models.player import Player
from app.models.squad import SquadPlayer, SquadState
from app.models.transfer import TransferHistory
from app.services.squad.mappers import (
    compute_chips_available,
    compute_free_transfers,
    map_squad_players,
)


@dataclass
class SquadSyncResult:
    """Outcome of syncing one gameweek's squad state."""

    gameweek: int
    state_created: bool
    players_created: int
    players_updated: int
    players_removed: int
    free_transfers: int
    chips_available: list[str]
    chip_played: str | None
    bank_balance: int
    squad_value: int


class SquadSyncService:
    """Persists a manager's FPL squad picks into the database, gameweek by gameweek.

    Stateless aside from the `Session` passed per call, matching the
    convention of every other `*Service` in this project.
    """

    def sync_from_fpl_payloads(
        self,
        db: Session,
        gameweek: int,
        picks_payload: dict,
    ) -> SquadSyncResult:
        """Upsert the `SquadState` (+ its `SquadPlayer` rows) for `gameweek`.

        Args:
            db: Active SQLAlchemy session (caller commits, per the
                project's `session_scope()` convention).
            gameweek: The gameweek this picks payload is for.
            picks_payload: The raw dict returned by
                `FPLAPIClient.get_entry_event_picks(entry_id, gameweek)` -
                expected keys: `entry_history` (bank/value/rank/points),
                `active_chip`, and `picks` (the 15-player list).
        """
        entry_history = picks_payload.get("entry_history", {})
        active_chip = picks_payload.get("active_chip")
        picks = picks_payload.get("picks", [])

        previous_state = (
            db.query(SquadState)
            .filter(SquadState.gameweek < gameweek)
            .order_by(SquadState.gameweek.desc())
            .first()
        )

        transfers_made_previous_gw = 0
        if previous_state is not None:
            transfers_made_previous_gw = (
                db.query(TransferHistory)
                .filter_by(gameweek=previous_state.gameweek)
                .count()
            )

        free_transfers = compute_free_transfers(
            previous_state,
            transfers_made_previous_gw,
            chip_played_previous_gw=previous_state.chip_played if previous_state else None,
        )

        chip_history = [
            state.chip_played
            for state in db.query(SquadState).filter(SquadState.gameweek < gameweek).all()
        ]
        chips_available = compute_chips_available(chip_history, active_chip)

        squad_state = db.query(SquadState).filter_by(gameweek=gameweek).one_or_none()
        state_created = squad_state is None

        state_fields = {
            "bank_balance": entry_history.get("bank", 0),
            "squad_value": entry_history.get("value", 0),
            "free_transfers": free_transfers,
            "chips_available": chips_available,
            "chip_played": active_chip,
            "overall_rank": entry_history.get("overall_rank"),
            "total_points": entry_history.get("total_points", 0),
        }

        if squad_state is None:
            squad_state = SquadState(gameweek=gameweek, **state_fields)
            db.add(squad_state)
            db.flush()
        else:
            for key, value in state_fields.items():
                setattr(squad_state, key, value)
            db.flush()

        players_created, players_updated, players_removed = self._sync_players(
            db, squad_state, picks
        )

        db.flush()
        logger.info(
            "Squad sync for gw {} complete: {} player(s) created, {} updated, "
            "{} removed; {} free transfer(s), chip_played={}",
            gameweek,
            players_created,
            players_updated,
            players_removed,
            free_transfers,
            active_chip,
        )

        return SquadSyncResult(
            gameweek=gameweek,
            state_created=state_created,
            players_created=players_created,
            players_updated=players_updated,
            players_removed=players_removed,
            free_transfers=free_transfers,
            chips_available=chips_available,
            chip_played=active_chip,
            bank_balance=squad_state.bank_balance,
            squad_value=squad_state.squad_value,
        )

    @staticmethod
    def _sync_players(
        db: Session, squad_state: SquadState, picks: list[dict]
    ) -> tuple[int, int, int]:
        """Upsert `SquadPlayer` rows for `squad_state`, removing anyone transferred out.

        Returns:
            (created, updated, removed) counts.
        """
        existing_by_player = {sp.player_id: sp for sp in squad_state.players}
        seen_ids: set[int] = set()
        created = 0
        updated = 0

        for fields in map_squad_players(picks):
            player_id = fields["player_id"]
            seen_ids.add(player_id)

            player = db.get(Player, player_id)
            now_cost = player.now_cost if player is not None else 0

            existing = existing_by_player.get(player_id)
            if existing is None:
                db.add(
                    SquadPlayer(
                        squad_state_id=squad_state.id,
                        player_id=player_id,
                        purchase_price=now_cost,
                        selling_price=now_cost,
                        is_starting=fields["is_starting"],
                        bench_position=fields["bench_position"],
                        is_captain=fields["is_captain"],
                        is_vice_captain=fields["is_vice_captain"],
                    )
                )
                created += 1
            else:
                existing.is_starting = fields["is_starting"]
                existing.bench_position = fields["bench_position"]
                existing.is_captain = fields["is_captain"]
                existing.is_vice_captain = fields["is_vice_captain"]
                # Purchase price is left untouched (it's a historical
                # fact); selling price is refreshed to the current market
                # price as our best available approximation - see the
                # module-level note in mappers.py on why this isn't exact.
                existing.selling_price = now_cost
                updated += 1

        removed = 0
        for player_id, existing in existing_by_player.items():
            if player_id not in seen_ids:
                db.delete(existing)
                removed += 1

        return created, updated, removed
