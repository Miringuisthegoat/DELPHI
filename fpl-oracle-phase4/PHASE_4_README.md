# Phase 4 — Data Collection & Storage

## What this delivers

`DataIngestionService`: the layer that takes the typed Pydantic models
Phase 3's `FPLAPIClient` fetches and persists them into the `teams`,
`players`, `fixtures`, and `player_gameweek_stats` tables built in Phase 2.
This is what turns "we can call the FPL API" into "the database has a
growing, queryable history" — the foundation everything from Phase 5
(prediction engine) onward reads from.

Also included: two small, additive schema fields (`transfers_in_event` /
`transfers_out_event` on `FPLPlayer`, and `expected_goals` /
`expected_assists` / `expected_goal_involvements` /
`expected_goals_conceded` / `starts` on `FPLElementHistory`) needed to map
a couple of `Player`/`PlayerGameweekStats` columns faithfully. Both files
already use `extra="ignore"`, so these are safe, backward-compatible
additions — nothing from Phase 3 breaks.

## Files, and where they go in your existing tree

```
app/
├── schemas/
│   ├── fpl_bootstrap.py                 # MODIFIED — 2 new fields on FPLPlayer
│   └── fpl_element_summary.py           # MODIFIED — 5 new fields on FPLElementHistory
├── services/
│   └── ingestion/                       # NEW package
│       ├── __init__.py
│       ├── mappers.py                   # pure schema -> ORM field-dict functions
│       └── service.py                   # DataIngestionService (the upsert logic)
├── api/
│   └── v1/
│       └── endpoints/
│           └── sync.py                  # NEW — routes that trigger a sync
└── main.py                              # MODIFIED — registers sync router + init_db() on startup
scripts/
└── sync_data.py                         # NEW — CLI: python -m scripts.sync_data
tests/
└── test_ingestion_service.py            # NEW — 14 tests
```

## Design decisions worth knowing about

**Upsert by natural FPL id, never rebuild.** `Team`, `Player`, and
`Fixture` all use FPL's own ids as primary keys (set up back in Phase 2),
so every sync loads the existing row by id and updates it in place if
present, or inserts if not. Re-running a sync is always safe and never
duplicates data — this mirrors the app's core philosophy of evolving
state incrementally rather than wiping and regenerating it.

**Two sources for `player_gameweek_stats`, on purpose:**
- `sync_player_history()` reads `element-summary`'s `history` array. This
  is the *accurate* source — it includes the exact fixture and price at
  the time — but the endpoint is per-player, so it's meant for backfilling
  specific players of interest (your squad, transfer targets), not all
  ~700 players every week.
- `sync_gameweek_live()` reads `event/{id}/live`, which covers every
  player in one call and is available before `element-summary` settles,
  but doesn't include price/ownership/form, so those are copied from the
  already-synced `Player` row as a same-day approximation.

Both upsert against the same `(player_id, gameweek)` unique constraint
from Phase 2, so running both for the same gameweek just refines the row
rather than creating duplicates.

**Partial failure is handled, not fatal.** Every `sync_*` method loops
over its input and catches per-item exceptions (an unrecognised
`element_type`, a malformed field) into the returned `IngestionResult`
rather than letting one bad player abort a sync of hundreds. Check
`result.failed` and `result.errors` after any sync.

**No commits inside the service.** Every `sync_*` method calls
`db.flush()` (so autoincrement ids and constraint violations surface
immediately) but never `db.commit()` — that's left to the caller via
`session_scope()`, so `sync_full_bootstrap()` (teams + players + fixtures)
commits as one atomic transaction.

## Running it

```powershell
# One-off full sync (teams, players, fixtures):
python -m scripts.sync_data

# Also pull live stats for the current gameweek:
python -m scripts.sync_data --gameweek 8

# Also backfill full history for specific players (e.g. your squad):
python -m scripts.sync_data --history 328 351 401
```

Or via the API (once `uvicorn app.main:app --reload` is running):

```
POST /api/v1/sync/full
POST /api/v1/sync/players/{player_id}/history
POST /api/v1/sync/gameweeks/{gameweek}/live
```

Each returns a JSON summary: `{created, updated, failed, errors}` per
collection synced.

## Tests

```powershell
pytest tests/test_ingestion_service.py -v
```

14 tests covering: team/player/fixture create + update-in-place, position
mapping via `element_type`, unknown-status and unknown-position fallback
behaviour, price-trend calculation, history backfill + re-run dedup, live
gameweek sync (including the "player not yet synced" failure path), and
the full orchestrated `sync_full_bootstrap()`.

All 29 project tests (15 from Phase 2 + 14 new) pass.

## Not done in this phase (by design)

- **Scheduling** (APScheduler wiring so this runs automatically every
  Tuesday morning) is Phase 4's natural next step but was left out here
  to keep this delivery focused on the ingestion logic itself; the
  `enable_scheduler` / `weekly_update_cron` settings already exist in
  `app/core/config.py` waiting for it.
- **"My Squad" sync** (pulling *your* specific team via `/entry/{id}/`)
  reuses `client.get_entry()` / `client.get_entry_event_picks()`, already
  wired in the Phase 3 client, but writing those into `SquadState` /
  `SquadPlayer` is scoped to Phase 7 (Squad Management) per the master
  plan, since it needs transfer-cost/chip logic this phase doesn't have yet.
