# Phase 5 — Prediction Engine (DELPHI)

## What this delivers

**DELPHI** (Data-driven Expected League Performance using Hybrid
Intelligence) — the app's points-prediction engine. This is what turns
Phase 4's growing database of gameweek history into the "predict future
performance" and "learning system" pieces of the project brief.

"Hybrid" because DELPHI is two predictors behind one interface:

- **`HeuristicPredictor`** — a transparent, rules-based estimate (position
  baseline × price × fixture difficulty × home advantage × expected
  minutes) that needs no training data at all. This is what makes DELPHI
  usable from day one of a season — your database currently has **0**
  `player_gameweek_stats` rows (preseason), and the heuristic is exactly
  what covers that gap.
- **`RandomForestPointsPredictor`** — a scikit-learn Random Forest trained
  on accumulated gameweek history, used automatically once enough of it
  exists (`ML_MIN_SAMPLES_FOR_TRAINING`, default 200 rows).

`DelphiPredictionEngine` picks whichever is appropriate on every call —
no manual switch needed as the season progresses.

## Files, and where they go in your existing tree

```
app/
├── core/
│   └── config.py                        # MODIFIED — new ml_* settings block
├── ml/                                   # NEW package (was an empty stub)
│   ├── __init__.py                       # public exports
│   ├── features.py                       # PlayerFeatureBuilder, FeatureVector
│   ├── heuristic.py                      # HeuristicPredictor (cold start)
│   ├── model.py                          # PointsPredictorModel + RandomForestPointsPredictor
│   ├── training.py                       # ModelTrainingService
│   └── engine.py                         # DelphiPredictionEngine (orchestrator)
├── schemas/
│   └── prediction.py                     # NEW — API response schemas
├── api/
│   └── v1/
│       └── endpoints/
│           └── predictions.py            # NEW — /api/v1/predictions/* routes
└── main.py                               # MODIFIED — registers predictions router
scripts/
├── train_model.py                        # NEW — CLI: python -m scripts.train_model
└── generate_predictions.py               # NEW — CLI: python -m scripts.generate_predictions
tests/
├── test_features.py                      # NEW — 5 tests
├── test_heuristic.py                     # NEW — 6 tests
├── test_model.py                         # NEW — 5 tests
└── test_prediction_engine.py             # NEW — 4 tests
requirements.txt                          # MODIFIED — joblib pinned explicitly
```

All 49 tests pass (`pytest tests/`), including the 29 from Phases 1–4.

## Design decisions worth knowing about

**No-lookahead feature engineering, enforced at the source.**
`PlayerFeatureBuilder.build(db, player, target_gameweek)` only ever reads
`PlayerGameweekStats` rows with `gameweek < target_gameweek`. This is
what makes an offline MAE/RMSE number trustworthy later — the model
never gets to see the answer before "predicting" it. Fixture-dependent
fields (difficulty, home/away, opponent strength) *do* describe the
target gameweek, since real fixtures are known in advance.

**33 features, one shared vector.** Both predictors consume the exact
same `FeatureVector` (price, position, rolling 3/5/season averages,
weighted recent form, ICT components, rotation risk, expected-minutes
probability, fixture difficulty, home/away, double/blank-gameweek
awareness, team/opponent strength). This means swapping in a different
model later (XGBoost, per the project's "Future Features" list) only
means writing a new class in `model.py` — feature engineering doesn't
change.

**Cold start is a first-class path, not an afterthought.** With zero
gameweek history, `HeuristicPredictor` falls back to price as the
primary quality signal (the market has already priced in reputation and
expected role) with position-specific baselines and fixture/home
adjustments layered on top. As soon as a few gameweeks of history exist
for a player, the heuristic starts blending in observed form; once the
*whole database* has enough rows (200+ by default), the engine switches
to the trained Random Forest automatically.

**Explainability is not optional.** Every heuristic prediction returns a
`reasoning` string built from the same factors that shaped the number
("a favourable fixture (difficulty 2/5); playing at home; only a 75%
chance of playing"). Random-Forest-era predictions get a lighter
per-gameweek note plus `feature_importances()` on the model itself for
deeper explanation.

**Horizons are sums of per-gameweek estimates, not one big guess.** A
3- or 5-gameweek prediction recomputes fixture-dependent features for
*each* gameweek in the window (a run of fixtures can swing from easy to
hard) and sums the results, while player-level rolling history stays
fixed (it's built from gameweeks strictly before the *first* gameweek in
the window). Confidence decays the further out the horizon goes.

**Predictions are upserted, never duplicated.** Same pattern as
`DataIngestionService`: `(player_id, gameweek, horizon)` is the natural
key. Re-running `generate` for a gameweek refines existing rows instead
of creating duplicates, and clears any stale `actual_points` so a
re-prediction isn't silently "pre-evaluated" against last time's numbers.

**Training refuses to run on too little data.** `ModelTrainingService.train()`
raises `PredictionError` (surfaced as HTTP 422) below
`ML_MIN_SAMPLES_FOR_TRAINING` rows, rather than silently fitting an
undertrained model that would perform worse than the heuristic it's
meant to improve on.

**The learning loop closes with `evaluate_gameweek`.** Once a gameweek's
results are synced (Phase 4), calling `evaluate_gameweek(gameweek)` finds
that gameweek's horizon-1 predictions, calls `Prediction.record_actual()`
(added back in Phase 2) to fill in `actual_points`/`prediction_error`,
and reports the batch's mean absolute error. Those rows are exactly what
the *next* `train()` call learns from.

## New settings (`app/core/config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `ml_model_dir` | `./data/processed/models` | Where trained artifacts + metadata JSON are saved |
| `ml_min_samples_for_training` | 200 | Minimum training rows before DELPHI trusts the Random Forest |
| `ml_rf_n_estimators` / `ml_rf_max_depth` / `ml_rf_min_samples_leaf` | 300 / 10 / 3 | Random Forest hyperparameters |
| `ml_default_horizons` | (1, 3, 5) | Default prediction horizons |
| `ml_model_name` | `delphi` | Base identifier stored on `Prediction.model_name` |

## Running it

```powershell
# Generate predictions for the upcoming gameweek (1/3/5-gameweek horizons):
python -m scripts.generate_predictions --gameweek 1

# Custom horizons:
python -m scripts.generate_predictions --gameweek 1 --horizons 1 5

# Once a gameweek has been played and synced (Phase 4), close the loop:
python -m scripts.generate_predictions --evaluate 1

# Train the Random Forest once enough history exists (needs 200+ rows):
python -m scripts.train_model
```

Or via the API (`uvicorn app.main:app --reload`):

```
POST /api/v1/predictions/train
POST /api/v1/predictions/generate/{gameweek}?horizons=1&horizons=3&horizons=5
POST /api/v1/predictions/evaluate/{gameweek}
GET  /api/v1/predictions/{gameweek}?horizon=1&player_id=328
```

## What's expected right now, in your database

Your `player_gameweek_stats` table is currently empty (preseason — no
gameweek has been played yet). That means:

- `generate` will use the **heuristic** predictor for every player — this
  is correct, expected behaviour, not a bug.
- `train` will return a 422 explaining there isn't enough data yet — also
  expected. Once a few gameweeks of the new season are synced via Phase
  4's `sync_gameweek_live`/`sync_player_history`, re-run `train` and
  `generate` will automatically start using the Random Forest instead.

## Merging into your existing project

```powershell
# From the folder where you extracted this zip, with your project at
# C:\path\to\fpl-oracle (adjust to your actual path):

Copy-Item -Path "app\core\config.py" -Destination "C:\path\to\fpl-oracle\app\core\config.py" -Force
Copy-Item -Path "app\ml\*" -Destination "C:\path\to\fpl-oracle\app\ml\" -Recurse -Force
Copy-Item -Path "app\schemas\prediction.py" -Destination "C:\path\to\fpl-oracle\app\schemas\prediction.py" -Force
Copy-Item -Path "app\api\v1\endpoints\predictions.py" -Destination "C:\path\to\fpl-oracle\app\api\v1\endpoints\predictions.py" -Force
Copy-Item -Path "app\main.py" -Destination "C:\path\to\fpl-oracle\app\main.py" -Force
Copy-Item -Path "scripts\train_model.py" -Destination "C:\path\to\fpl-oracle\scripts\train_model.py" -Force
Copy-Item -Path "scripts\generate_predictions.py" -Destination "C:\path\to\fpl-oracle\scripts\generate_predictions.py" -Force
Copy-Item -Path "tests\test_features.py","tests\test_heuristic.py","tests\test_model.py","tests\test_prediction_engine.py" -Destination "C:\path\to\fpl-oracle\tests\" -Force
Copy-Item -Path "requirements.txt" -Destination "C:\path\to\fpl-oracle\requirements.txt" -Force

# Then, from inside C:\path\to\fpl-oracle:
pip install -r requirements.txt --break-system-packages   # picks up joblib
python -m pytest tests\ -q
```

## Next phase

Phase 6 (Transfer Optimization) reads `Prediction` rows written here —
combined with the squad/budget/chip state from `app/models/squad.py` and
OR-Tools — to recommend the highest-value transfer(s) for the week,
including evaluating point-hit trade-offs.
