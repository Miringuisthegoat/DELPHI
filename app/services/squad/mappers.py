"""
Phase 7: pure translation/calculation functions for syncing "My Squad".

Kept free of database/session logic (same convention as
`app.services.ingestion.mappers`) so every rule here is unit-testable in
isolation. `SquadSyncService` (in `service.py`) is the only caller.

Two things the public FPL API does *not* expose without authenticating as
the manager (the `/my-team/{id}/` endpoint, which needs a logged-in
session cookie, not just an API key):

1. **True purchase/selling price.** `/entry/{id}/event/{gw}/picks/` tells
   us *which* players are owned, but not what each was bought for, or
   what FPL's 50%-profit-rounding rule currently allows selling them for.
   As a documented approximation, both `purchase_price` and
   `selling_price` are set to the player's *current* `now_cost` on every
   sync. This is exact for players bought at today's price and a slight
   overestimate of sell value for anyone who has risen in price since
   being bought - acceptable for a planning tool, but worth knowing about
   if the numbers look slightly optimistic.
2. **Remaining free transfers / chips used.** These aren't returned
   directly either, so both are *derived* here from data we already
   store ourselves (`TransferHistory` rows, and previous `SquadState`
   rows) rather than trusted blindly from any single API field.
"""

from __future__ import annotations

from app.models.enums import ChipType
from app.models.squad import SquadState

MAX_FREE_TRANSFERS = 5
"""FPL's cap on banked free transfers (introduced 2024/25 rules)."""

_CHIPS_THAT_DONT_CONSUME_A_TRANSFER = {ChipType.WILDCARD.value, ChipType.FREE_HIT.value}
"""Playing a wildcard or free hit lets you make unlimited transfers that
gameweek without spending (or losing) a banked free transfer."""

ALL_CHIP_NAMES: tuple[str, ...] = tuple(c.value for c in ChipType)


def map_squad_players(picks: list[dict]) -> list[dict]:
    """Map the `picks` array of an `event/{gw}/picks/` payload to field dicts.

    FPL's `position` is 1-15 (1-11 starting XI in formation order, 12-15
    bench in playing-order-if-needed); `multiplier` of 2/3 signals
    (vice-)captaincy but `is_captain`/`is_vice_captain` flags are more
    direct and used here instead.
    """
    results: list[dict] = []
    for pick in picks:
        position = pick["position"]
        is_starting = position <= 11
        results.append(
            {
                "player_id": pick["element"],
                "is_starting": is_starting,
                "bench_position": None if is_starting else position - 11,
                "is_captain": bool(pick.get("is_captain", False)),
                "is_vice_captain": bool(pick.get("is_vice_captain", False)),
            }
        )
    return results


def compute_free_transfers(
    previous_state: SquadState | None,
    transfers_made_previous_gw: int,
    chip_played_previous_gw: str | None,
) -> int:
    """Derive free transfers available for the gameweek being synced.

    Rule: each gameweek you either use your free transfer(s) or bank one
    more (capped at `MAX_FREE_TRANSFERS`), never dropping below 1. Wildcard
    and Free Hit gameweeks are transfer-neutral - any transfers made under
    those chips don't consume or reduce the count.
    """
    if previous_state is None:
        return 1

    if chip_played_previous_gw in _CHIPS_THAT_DONT_CONSUME_A_TRANSFER:
        transfers_made_previous_gw = 0

    remaining_after_use = max(previous_state.free_transfers - transfers_made_previous_gw, 0)
    return min(remaining_after_use + 1, MAX_FREE_TRANSFERS)


def compute_chips_available(
    chip_history: list[str | None], active_chip_this_gw: str | None
) -> list[str]:
    """Return chip identifiers not yet used, given every past gameweek's `chip_played`.

    Args:
        chip_history: `chip_played` values from every `SquadState` row
            strictly before the gameweek being synced.
        active_chip_this_gw: The chip played *in* the gameweek being
            synced (if any) - already-spent as of this row, so excluded
            from what's still available.
    """
    used = {c for c in chip_history if c}
    if active_chip_this_gw:
        used.add(active_chip_this_gw)
    return [c for c in ALL_CHIP_NAMES if c not in used]
