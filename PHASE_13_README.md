# Phase 13 — Defensive Contribution Scoring (2025-26 FPL rules)

## What this delivers

End-to-end wiring for FPL's 2025-26 **defensive contribution** scoring
rule (DEF: 10+ CBIT actions, MID/FWD: 12+ combined actions = +2 bonus
points per gameweek) through every layer DELPHI touches: live FPL API
ingestion, Phase 12's historical CSV ingestion, and the `FeatureVector`
the Random Forest actually trains on. Before this phase, DELPHI was
blind to this points source entirely — a real scoring signal for the
season that matters most (recent/current data) was being silently
dropped.

```
app/
├── models/
│   ├── player_stats.py                    # MODIFIED — 4 new columns
│   └── player_stats_historical.py          # MODIFIED — same 4 columns (Phase 12 table)
├── schemas/
│   ├── fpl_element_summary.py              # MODIFIED — FPLElementHistory +4 fields
│   └── fpl_live.py                         # MODIFIED — FPLLiveStats +4 fields
├── services/
│   ├── ingestion/
│   │   └── mappers.py                      # MODIFIED — map_history_row/map_live_element carry the fields
│   └── historical/
│       ├── fetcher.py                      # HOTFIX — corrected merged_gw.csv URL (was missing `data/` prefix)
│       ├── service.py                      # HOTFIX — double-gameweek rows now summed, not colliding on unique key
│       └── mappers.py                      # MODIFIED — vaastav CSV column aliases + defaults
├── ml/
│   ├── features.py                         # MODIFIED — 4 new FEATURE_NAMES + rolling averages
│   └── training.py                         # MODIFIED — docstring only (no logic change)
tests/
└── test_defensive_contribution.py          # NEW — 10 tests
requirements.txt                            # unchanged
```

**Bundled hotfixes, unrelated to defensive contribution itself but needed
to actually get data in:**
1. `fetcher.py`'s URL template was missing a `data/` path segment
   (`.../master/{season}/gws/merged_gw.csv` instead of the repo's real
   layout, `.../master/data/{season}/gws/merged_gw.csv`), which made
   every season 404 identically regardless of season string.
2. `service.py` didn't handle vaastav's `merged_gw.csv` listing
   double-gameweek fixtures as *separate rows* (same player, same `GW`,
   different underlying fixture) - combined with this project's
   `autoflush=False` session default, two rows for the same
   `(season, source_name, gameweek)` key within one CSV would both look
   "new" to the in-loop existence check and collide on the unique
   constraint at flush time. Fixed by tracking keys upserted within the
   current run in memory and **summing** numeric stats (minutes, points,
   goals, defensive contribution, etc.) when a genuine duplicate shows
   up, rather than colliding or silently overwriting one fixture with
   the other.

If you already manually applied either fix, these copies are identical
to what you have - safe to overwrite either way.

## Design decisions worth knowing about

**Four new columns, same shape everywhere.** `clearances_blocks_interceptions`,
`tackles`, `recoveries`, `defensive_contribution` were added identically
to `PlayerGameweekStats` (live data) and `HistoricalPlayerGameweekStats`
(Phase 12's prior-season table), and to the two FPL API schemas
(`FPLElementHistory`, `FPLLiveStats`) and the vaastav CSV mapper. Every
one of them defaults to 0 - correct behaviour for any gameweek/season
before the rule existed, not a data-quality gap. `defensive_contribution`
itself is stored as FPL's own already-computed indicator (whether the
threshold was crossed that gameweek) rather than recomputed from the raw
CBIT count, since FPL may tune the threshold between seasons and their
own field is the source of truth.

**`FEATURE_NAMES` grew by four columns - this is a breaking change to
the model's input shape.** `cbi_avg_5`, `tackles_avg_5`, `recoveries_avg_5`,
`defensive_contribution_avg_5` (rolling 5-gameweek averages, same pattern
as `bonus_avg_5`/`bps_avg_5`) were added to `FeatureVector`/`FEATURE_NAMES`
in `app/ml/features.py`. Any Random Forest artifact trained before this
phase will **fail to load** afterward - `RandomForestPointsPredictor.load()`
already checks `stored_features != FEATURE_NAMES` and raises `ValueError`
(this check existed since Phase 5, not new here). This is intentional:
an old model was never shown this signal and has no learned relationship
for it, so silently continuing to use it would be worse than refusing to
load. **You must retrain after merging this phase** - see below.

**Historical rows dilute correctly, not artificially.** A player's
`cbi_avg_5` rolling average over a 5-gameweek window that spans, say, 2
pre-2025-26 gameweeks (0s) and 3 2025-26 gameweeks (real values) will
correctly show a lower average than 5 gameweeks of real activity - this
is accurate (the stat genuinely didn't exist yet), not a bug to work
around. No special-casing was added for this; it falls out naturally
from every field defaulting to 0.

**No change to `HeuristicPredictor`.** The cold-start heuristic still
doesn't consider defensive contribution at all - out of scope here, and
arguably lower priority than the trained-model path, since heuristic
predictions are inherently coarse-grained already. Flag if you want that
folded in too; it'd be a small, separate addition to `heuristic.py`'s
scoring formula.

**No new dependencies.** Every change here reuses existing patterns
(`_num()`'s already-safe-default parsing in the historical mapper,
Pydantic field defaults in the live schemas) — nothing new to install.

## Required manual step: bump `ml_model_version`

`app/core/config.py`'s `ml_model_version` (currently `"1.0.0"`) is used
in `Prediction.model_version` and the saved-artifact filename
(`{name}_v{version}.joblib`). Since this phase changes the feature
schema, bump it so old and new artifacts/predictions are clearly
distinguishable in your data, rather than silently overwriting:

```python
# app/core/config.py
ml_model_version: str = Field(default="1.1.0")
```

This isn't strictly required for the code to work (the `stored_features`
check catches a stale artifact regardless), but it makes `Prediction`
rows and saved model files self-documenting about which feature schema
produced them - useful once you're comparing MAE across schema versions.

## Running it

```powershell
# Retraining is mandatory after this merge - your existing artifact
# will refuse to load (see above).
python -m scripts.train_model

# If you haven't already pulled 2025-26 data (the only season with real
# defensive-contribution values):
python -m scripts.sync_historical_data --seasons 2025-26

# Re-generate predictions once retrained:
python -m scripts.generate_predictions --gameweek <current_gw>
```

## Tests

```powershell
pytest tests\test_defensive_contribution.py -v
```

10 tests covering: live-API history/live-gameweek mappers carrying the
four fields through (and defaulting correctly when absent), the vaastav
CSV mapper handling both 2025-26-style and pre-2025-26-style rows,
`FEATURE_NAMES` containing the four new columns, rolling-average
computation from real `PlayerGameweekStats` history (including a window
that mixes pre-rule and post-rule gameweeks), and the cold-start case
still producing a valid, correctly-shaped, all-zero vector.

## Merging into your existing project

Unzip `fpl-oracle-phase13.zip` into your project root (creates
`.\fpl-oracle-phase13\`), then from your project root:

```powershell
Copy-Item ".\fpl-oracle-phase13\app\models\player_stats.py" -Destination ".\app\models\player_stats.py" -Force
Copy-Item ".\fpl-oracle-phase13\app\models\player_stats_historical.py" -Destination ".\app\models\player_stats_historical.py" -Force
Copy-Item ".\fpl-oracle-phase13\app\schemas\fpl_element_summary.py" -Destination ".\app\schemas\fpl_element_summary.py" -Force
Copy-Item ".\fpl-oracle-phase13\app\schemas\fpl_live.py" -Destination ".\app\schemas\fpl_live.py" -Force
Copy-Item ".\fpl-oracle-phase13\app\services\ingestion\mappers.py" -Destination ".\app\services\ingestion\mappers.py" -Force
Copy-Item ".\fpl-oracle-phase13\app\services\historical\fetcher.py" -Destination ".\app\services\historical\fetcher.py" -Force
Copy-Item ".\fpl-oracle-phase13\app\services\historical\service.py" -Destination ".\app\services\historical\service.py" -Force
Copy-Item ".\fpl-oracle-phase13\app\services\historical\mappers.py" -Destination ".\app\services\historical\mappers.py" -Force
Copy-Item ".\fpl-oracle-phase13\app\ml\features.py" -Destination ".\app\ml\features.py" -Force
Copy-Item ".\fpl-oracle-phase13\app\ml\training.py" -Destination ".\app\ml\training.py" -Force
Copy-Item ".\fpl-oracle-phase13\tests\test_defensive_contribution.py" -Destination ".\tests\test_defensive_contribution.py" -Force
Copy-Item ".\fpl-oracle-phase13\PHASE_13_README.md" -Destination ".\PHASE_13_README.md" -Force
Remove-Item ".\fpl-oracle-phase13\" -Recurse -Force
```

Then the manual `ml_model_version` bump described above, and:

```powershell
python -m pytest tests\ -q
python -m scripts.train_model
```

**Note on your existing database schema:** since `init_db()` only runs
`Base.metadata.create_all()` (creates missing tables, never alters
existing ones - see `app/db/session.py`), the four new columns on
`player_gameweek_stats` and `player_gameweek_stats_historical` **will
not appear automatically** if those tables already exist from a prior
run. Either delete `data/fpl_oracle.db` and let `init_db()` recreate it
from scratch (fine for a dev SQLite setup with re-syncable data), or add
a small Alembic migration for the 8 new columns if you'd rather preserve
existing rows. Since your project already has `alembic` in
`requirements.txt` but I don't see it actively used yet (no `alembic/`
folder in what you've shared), flag if you want me to scope out getting
Alembic actually wired up as its own small phase — worth doing before
this happens again on some future field addition.

## What's deliberately *not* in this phase

- **`HeuristicPredictor` scoring changes** — flagged above as a smaller
  follow-up.
- **Alembic migration for the new columns** — flagged above; currently
  your only path to picking up the new columns on an existing database
  is deleting and re-syncing, or a manual `ALTER TABLE`.
- **Backfilling `defensive_contribution` for 2025-26 rows already
  ingested via live sync before this phase existed** — if you ran
  `sync_data`/`sync_player_history` against the live API earlier in the
  2025-26 season before this phase was merged, those rows were written
  with the old mapper and won't have these fields populated. Re-run
  `sync_player_history` for affected players (or `sync_gameweek_live`
  for affected gameweeks) after merging to backfill them — the upsert
  logic in `DataIngestionService` will update existing rows in place.
