# Phase 12 — Historical Season Pretraining (vaastav/Fantasy-Premier-League)

## What this delivers

A one-off ingestion pipeline pulling prior FPL seasons from the
community-maintained [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League)
repo into a new `player_gameweek_stats_historical` table, name-matched to
your current `players` table where possible, plus a `ModelTrainingService`
change so DELPHI's Random Forest can pretrain on that data instead of
waiting for ~200 in-season rows to accumulate.

**Design choice (Option A from the original scoping discussion):** a
separate table, joined to current players by fuzzy name-matching at
ingestion time — not a schema change to `players`/`player_gameweek_stats`.
Non-invasive and reversible; a future `player_code`-based exact-join
migration (Option B) remains open if the match rate here isn't good enough.

```
app/
├── models/
│   └── player_stats_historical.py        # NEW — HistoricalPlayerGameweekStats
├── services/
│   └── historical/                        # NEW package
│       ├── __init__.py
│       ├── fetcher.py                      # downloads merged_gw.csv per season
│       ├── mappers.py                      # CSV row -> field dict (column-alias tolerant)
│       ├── name_matcher.py                 # exact -> exact -> fuzzy name resolution
│       └── service.py                      # HistoricalIngestionService (upsert)
├── ml/
│   └── training.py                         # MODIFIED — blends historical rows into training
scripts/
└── sync_historical_data.py                 # NEW — CLI
tests/
└── test_historical_ingestion.py            # NEW — 10 tests
requirements.txt                            # MODIFIED — adds rapidfuzz
```

## Design decisions worth knowing about

**Name matching, three tiers, always audited.** `PlayerNameMatcher` tries
exact `web_name`, then exact normalised full name, then `rapidfuzz`
token-sort-ratio above 88 — anything weaker is left `unmatched` rather
than guessed. Every row records `match_method` + `match_confidence`, so
you can query how confident any given historical row's link is, and
`HistoricalIngestionResult.match_rate` reports the season-level rate so
you know at a glance whether Option A is holding up (a low rate is your
signal to consider the `player_code` migration mentioned in the original
scoping notes).

**Unmatched rows aren't discarded.** They still get stored (with
`matched_player_id = NULL`) because they're auditable and because a
future enrichment could re-match them, but `ModelTrainingService` only
trains on *matched* rows today, since building a feature vector needs a
current `Player` row to hang position/price context off of.

**No no-lookahead rule for historical data.** Phase 5's `PlayerFeatureBuilder`
strict "only gameweeks before the target" rule exists to stop *current,
in-progress* predictions from leaking the answer. A past, fully-played
season has no such risk — every gameweek in it is fair game as a labelled
example, so `_build_historical_rows()` doesn't filter by gameweek at all.

**Feature vectors for historical rows use TODAY's player context, not
back-then's.** A historical row's label (`total_points`) is real, but its
feature vector is built from the player's *current* price/position/team via
`PlayerFeatureBuilder` — not reconstructed as of that old gameweek. This is
a deliberate simplification: the goal is teaching the model general
price/form/fixture → points relationships, not perfectly time-traveling
context. Flagged in the code as worth revisiting if evaluation shows it
hurts more than it helps (a truer version would need historical
price-at-the-time and historical fixture difficulty joined in too).

**`build_training_data()`'s return signature changed** from `(X, y)` to
`(X, y, historical_row_count)` — a small breaking change from Phase 5's
version. `train()`'s own signature is backward compatible (`include_historical`
defaults to `True`, and safely no-ops if this phase hasn't been merged in
yet, via an `ImportError`-guarded import at the top of `training.py`).

**Never part of the scheduled pipeline.** Past seasons don't change —
`sync_historical_data.py` is a manual, occasional CLI run, never called by
`WeeklyPipelineService` or any APScheduler job.

**Column-alias tolerant, like the FPL API schemas.** vaastav's CSV schema
has drifted across seasons (xG/xA columns added ~2021/22, occasional
renames). `mappers.resolve_columns()` picks the first available alias per
logical field and defaults anything missing to 0.0/None, rather than
raising — the same philosophy as `extra="ignore"` on your `FPLPlayer`/
`FPLElementHistory` schemas.

## New dependency

```
rapidfuzz==3.10.1
```

## Running it

```powershell
# Pull 3 prior seasons (season strings match vaastav's folder naming):
python -m scripts.sync_historical_data --seasons 2021-22 2022-23 2023-24

# Then retrain DELPHI with the new pretraining data:
python -m scripts.train_model
```

Check the match rate in the log output — if it's well below ~85-90%,
that's the signal to revisit the Option B (`player_code`) join before
trusting the pretrained model too heavily.

## Tests

```powershell
pytest tests\test_historical_ingestion.py -v
```

10 tests covering: column-alias resolution, missing-required-column
rejection, row mapping with and without optional columns, all three
name-match tiers (exact web_name, exact full name, fuzzy) plus the
unmatched case, season ingestion upserting both matched and unmatched
rows, re-running a season updates rather than duplicates, and a fetch
failure being collected into the result rather than raised.

## Merging into your existing project

Unzip `fpl-oracle-phase12.zip` into your project root — it creates a
`.\fpl-oracle-phase12\` subfolder. Then, from your project root:

```powershell
Copy-Item ".\fpl-oracle-phase12\app\models\player_stats_historical.py" -Destination ".\app\models\player_stats_historical.py" -Force
Copy-Item ".\fpl-oracle-phase12\app\services\historical" -Destination ".\app\services\historical" -Recurse -Force
Copy-Item ".\fpl-oracle-phase12\app\ml\training.py" -Destination ".\app\ml\training.py" -Force
Copy-Item ".\fpl-oracle-phase12\scripts\sync_historical_data.py" -Destination ".\scripts\sync_historical_data.py" -Force
Copy-Item ".\fpl-oracle-phase12\tests\test_historical_ingestion.py" -Destination ".\tests\test_historical_ingestion.py" -Force
Copy-Item ".\fpl-oracle-phase12\requirements.txt" -Destination ".\requirements.txt" -Force
Copy-Item ".\fpl-oracle-phase12\PHASE_12_README.md" -Destination ".\PHASE_12_README.md" -Force
Remove-Item ".\fpl-oracle-phase12\" -Recurse -Force

pip install -r requirements.txt --break-system-packages
```

**One manual step this zip can't do for you:** register the new model on
`Base.metadata` so `init_db()`/Alembic picks it up. Open `app\models\__init__.py`
and add:

```python
from app.models.player_stats_historical import HistoricalPlayerGameweekStats
```

...to both the import block and the `__all__` list. Then:

```powershell
python -m pytest tests\ -q
```

## What's deliberately *not* in this phase

- **Option B (`player_code` exact join)** — flagged throughout as the
  natural follow-up if the fuzzy-match rate isn't good enough in practice.
- **Career-prior features on `FeatureVector`** (e.g. `career_points_avg_season`) —
  additive but out of scope here; needs a `FEATURE_NAMES` version bump and
  retrain once this ingestion path is proven.
- **`HeuristicPredictor` changes** — still price-based; blending in a
  career prior for the cold-start heuristic is a separate, smaller
  follow-up once this table exists and is populated.
- **Comparing DELPHI against vaastav's own `xP` column** — `source_xp` is
  captured and stored for exactly this purpose, but no comparison/report
  logic consumes it yet.

## Next

Once ingested and retrained, re-run your existing evaluated-gameweek MAE
comparison to see whether pretraining actually improved accuracy before
leaning on it for Phase 11's chip-optimization go/no-go signal.
