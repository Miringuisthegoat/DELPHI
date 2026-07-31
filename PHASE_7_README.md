# Phase 7 — Squad Management

## What this delivers

`SquadSyncService`: pulls **your actual FPL squad** (via `FPL_TEAM_ID`,
already in your `.env`) into the `SquadState`/`SquadPlayer` tables built
in Phase 2. This is the missing piece Phase 6's `TransferOptimizerService`
has been waiting on since its first `_load_squad_state()` call — your log
shows exactly that: `No squad state found at or before gameweek 8. Sync
'my squad' before requesting an optimization.` After this phase, that
error goes away the first time you run a sync.

## Files, and where they go in your existing tree

```
app/
├── services/
│   └── squad/                           # NEW package
│       ├── __init__.py
│       ├── mappers.py                    # pure calc functions (FTs, chips, picks)
│       └── service.py                    # SquadSyncService (the upsert logic)
├── api/
│   └── v1/
│       └── endpoints/
│           └── squad.py                  # NEW — POST /sync/{gw}, GET /{gw}
└── main.py                               # MODIFIED — registers squad router
scripts/
└── sync_squad.py                         # NEW — CLI: python -m scripts.sync_squad --gameweek 8
tests/
└── test_squad_service.py                 # NEW — 9 tests
```

No new dependencies, no schema changes - `SquadState`/`SquadPlayer` and
`app/schemas/squad.py` already existed from Phase 2 and are reused as-is.

## Design decisions worth knowing about

**Two things the public FPL API won't give us, handled explicitly rather
than silently guessed:**

1. **True purchase/selling price.** The picks endpoint
   (`/entry/{id}/event/{gw}/picks/`) tells us *which* 15 players you own,
   not what you paid or what FPL's 50%-profit-rounding rule currently lets
   you sell them for - that detail lives behind the authenticated
   `/my-team/{id}/` endpoint (needs a logged-in session cookie, not just
   an API key), which is out of scope here. As a documented
   approximation, both `purchase_price` and `selling_price` are set to
   each player's *current* `now_cost` on every sync. This is exact for
   anyone bought at today's price, and a slight overestimate of sell
   value for players who've risen in price since — worth knowing if a
   `bank_after` figure from the optimizer looks a touch generous. Revisit
   if/when authenticated access is added.
2. **Remaining free transfers and chips used.** Also not directly
   returned, so both are *derived* from data this project already owns
   rather than trusted blindly:
   - **Free transfers**: `compute_free_transfers()` looks at the
     previous `SquadState.free_transfers` and how many `TransferHistory`
     rows exist for the previous gameweek, applying FPL's "use it or
     bank it (up to 5)" rule. A Wildcard/Free Hit gameweek is treated as
     transfer-neutral (per the real rules) - any transfers logged that
     week don't reduce next week's count.
   - **Chips available**: `compute_chips_available()` walks every past
     `SquadState.chip_played` value and subtracts whatever's already been
     used from the full `ChipType` set. (Note: this doesn't yet model the
     2024/25 rule granting a *second* wildcard partway through the
     season - it currently treats "wildcard" as a single one-time chip.
     Flag if you want that added; it's a small follow-up to
     `compute_chips_available`.)

**Upsert, and squad membership is authoritative.** Every sync loads the
existing `SquadState` for that gameweek (if any) and updates it in place;
`SquadPlayer` rows are upserted by `player_id` the same way. Critically,
any player who *was* in a previous sync's picks but isn't in the new
payload is deleted from that `SquadState` - the incoming picks list is
always the full, current truth for who's in your 15, not an additive log.

**No commits inside the service**, matching every other `*Service` in
this project - `sync_from_fpl_payloads()` flushes but leaves committing
to the caller's `session_scope()`.

## Running it

```powershell
# Sync your squad's picks for a specific gameweek (requires FPL_TEAM_ID in .env):
python -m scripts.sync_squad --gameweek 8
```

Or via the API (`uvicorn app.main:app --reload`):

```
POST /api/v1/squad/sync/8
GET  /api/v1/squad/8
```

Once this has run for the current (or a prior) gameweek, Phase 6's
optimizer works end-to-end:

```
POST /api/v1/predictions/generate/8      (Phase 5)
POST /api/v1/squad/sync/8                (this phase)
POST /api/v1/optimization/optimize/8     (Phase 6)
```

## Tests

```powershell
pytest tests/test_squad_service.py -v
```

9 tests covering: starting-XI/bench split from `position`, captain/vice
flags, first-ever sync grants 1 free transfer, banking an unused free
transfer, spending a free transfer, a dropped player being removed from
the squad on re-sync, wildcard-gameweek transfer-neutrality, and the
free-transfer/chip-availability calculator functions directly.

## Merging into your existing project

```powershell
Copy-Item ".\fpl-oracle-phase7\app\services\squad" -Destination ".\app\services\squad" -Recurse -Force
Copy-Item ".\fpl-oracle-phase7\app\api\v1\endpoints\squad.py" -Destination ".\app\api\v1\endpoints\squad.py" -Force
Copy-Item ".\fpl-oracle-phase7\app\main.py" -Destination ".\app\main.py" -Force
Copy-Item ".\fpl-oracle-phase7\scripts\sync_squad.py" -Destination ".\scripts\sync_squad.py" -Force
Copy-Item ".\fpl-oracle-phase7\tests\test_squad_service.py" -Destination ".\tests\test_squad_service.py" -Force
Copy-Item ".\fpl-oracle-phase7\PHASE_7_README.md" -Destination ".\PHASE_7_README.md" -Force
Remove-Item ".\fpl-oracle-phase7\" -Recurse -Force

python -m pytest tests\ -q
```

## Next phase

Phase 8 (Dashboard, FastAPI + Jinja2 + Neo-Brutalism CSS) surfaces
everything built so far - predictions (Phase 5), transfer suggestions
(Phase 6), and this phase's live squad state - in one page.
