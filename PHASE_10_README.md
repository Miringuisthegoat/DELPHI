# Phase 10 — Integration, Performance, and Hardening

## What this delivers

The final planned phase. Rather than new player-facing functionality,
this wires everything Phases 5–9 built into **one call** for the actual
weekly workflow, adds an **end-to-end integration test** that runs the
real prediction engine + real OR-Tools optimizer + real report builder
together (not mocked), and adds a **performance profiling script** for
the transfer optimizer against a realistically-sized (~600) player pool
- the check worth doing before relying on this for a real season.

```
app/
├── services/
│   └── pipeline/                        # NEW package
│       ├── __init__.py
│       └── service.py                    # WeeklyPipelineService, PipelineResult
├── schemas/
│   └── pipeline.py                       # NEW — API response schema
├── api/
│   └── v1/
│       └── endpoints/
│           └── pipeline.py               # NEW — POST /pipeline/run/{gameweek}
├── scheduler/
│   └── pipeline_jobs.py                  # NEW — opt-in heavier scheduled job
└── main.py                               # MODIFIED — registers the pipeline router
scripts/
├── run_weekly_pipeline.py                # NEW — CLI: python -m scripts.run_weekly_pipeline
└── profile_optimizer.py                  # NEW — CLI: python -m scripts.profile_optimizer
tests/
└── test_pipeline_integration.py          # NEW — 4 end-to-end tests
```

No new dependencies, no schema changes, nothing from Phases 1–9 modified
except `app/main.py` (one new router registration).

## Design decisions worth knowing about

**One orchestrator, zero new business logic.** `WeeklyPipelineService.run()`
calls `DelphiPredictionEngine.generate_for_gameweek()`, optionally
`DelphiPredictionEngine.evaluate_gameweek()`, then
`WeeklyReportService.build_report()` (which itself calls
`DashboardService`, which calls `TransferOptimizerService`) - in that
order, in one function. Every number it returns still comes from
exactly the services that already produced it in Phases 5–9; this class
only sequences calls and packages results, so there's still only one
place (`WeeklyReportService`) that could disagree with the dashboard
about "what DELPHI recommends."

**Live FPL sync deliberately stays out of the pipeline.** Syncing
bootstrap-static/fixtures/squad talks to an external, occasionally-down
API - a different failure domain than the purely-local predict ->
optimize -> report chain. Bundling them would mean one slow/failing sync
silently breaks reporting too, which is exactly the reasoning Phase 9's
scheduler job already used to keep sync out of the automatic weekly
report. Run `scripts.sync_data` / `scripts.sync_squad` (or their API
routes) first, same as every previous phase.

**Partial failure stays non-fatal, matching Phases 6–9's own
conventions.** No squad synced yet, no predictions generated yet, no
previous gameweek to evaluate - none of these raise. `evaluate_previous`
silently produces `evaluation=None` if there's nothing to backfill
(gameweek 1, or the previous gameweek hasn't been played/synced), and
the report itself already knows how to explain a missing squad or
missing predictions (Phase 8/9's empty states). Only prediction
*generation* failing is treated as fatal, since every downstream step
depends on it having produced something.

**The integration test is deliberately not mocked.** Every other test
file in this project exercises one service against a throwaway in-memory
SQLite database. `test_pipeline_integration.py` does the same, but seeds
a full mini "season" (teams, players, fixtures, a squad, and an
already-played previous gameweek) and runs the pipeline for real -
catching the class of bug that only shows up when Phase 5's predictor,
Phase 6's OR-Tools solver, Phase 7's squad state, and Phase 9's report
formatter are actually wired together, which no single phase's own unit
tests could catch alone.

**Performance profiling uses synthetic data, not your real database.**
`scripts/profile_optimizer.py` builds an entirely separate in-memory
SQLite database seeded with ~600 synthetic players across 20 clubs (a
realistic full-season player pool) and times
`TransferOptimizerService.optimize()` across several
`candidate_pool_size` values. This is the check worth running once
before the season gets busy: confirm solver time stays comfortably under
`_SOLVER_TIME_LIMIT_SECONDS` (8s) even at your chosen candidate pool
size, rather than discovering a slow solve during a live deadline.

**The scheduler still defaults to Phase 9's lighter, report-only job.**
`app/scheduler/pipeline_jobs.py` adds `generate_predict_and_report()` -
predict + evaluate + report as one job - as an *opt-in* alternative, but
`start_scheduler()` in `app/scheduler/jobs.py` is untouched and keeps
registering Phase 9's original report-only job. Swap which function is
registered (see `pipeline_jobs.py`'s module docstring) if you want the
scheduler to also regenerate predictions automatically each week; this
wasn't done by default since it changes what the existing
`ENABLE_SCHEDULER=true` setting does for anyone already relying on it.

## Running it

```powershell
# Full local pipeline for one gameweek (assumes squad already synced):
python -m scripts.run_weekly_pipeline --gameweek 8

# Skip evaluating the previous gameweek, or also deliver via console:
python -m scripts.run_weekly_pipeline --gameweek 8 --no-evaluate
python -m scripts.run_weekly_pipeline --gameweek 8 --send

# Profile the optimizer against a realistic ~600-player pool:
python -m scripts.profile_optimizer
python -m scripts.profile_optimizer --players 700 --pool-sizes 20 40 80
```

Or via the API (`uvicorn app.main:app --reload`):

```
POST /api/v1/pipeline/run/8
POST /api/v1/pipeline/run/8?evaluate_previous=false
POST /api/v1/pipeline/run/8?horizons=1&horizons=3&horizons=5
```

## Tests

```powershell
python -m pytest tests\test_pipeline_integration.py -v
python -m pytest tests\ -q     # full suite, all phases
```

4 new tests covering: the full predict -> evaluate -> report chain with
real squad/fixture/history data, re-running the pipeline twice without
duplicating predictions (confirms Phase 5's upsert guarantee holds
end-to-end), and running with no squad synced at all (confirms the
pipeline surfaces Phase 8/9's empty state instead of raising).

## Merging into your existing project

```powershell
Copy-Item ".\fpl-oracle-phase10\app\services\pipeline" -Destination ".\app\services\pipeline" -Recurse -Force
Copy-Item ".\fpl-oracle-phase10\app\schemas\pipeline.py" -Destination ".\app\schemas\pipeline.py" -Force
Copy-Item ".\fpl-oracle-phase10\app\api\v1\endpoints\pipeline.py" -Destination ".\app\api\v1\endpoints\pipeline.py" -Force
Copy-Item ".\fpl-oracle-phase10\app\scheduler\pipeline_jobs.py" -Destination ".\app\scheduler\pipeline_jobs.py" -Force
Copy-Item ".\fpl-oracle-phase10\app\main.py" -Destination ".\app\main.py" -Force
Copy-Item ".\fpl-oracle-phase10\scripts\run_weekly_pipeline.py" -Destination ".\scripts\run_weekly_pipeline.py" -Force
Copy-Item ".\fpl-oracle-phase10\scripts\profile_optimizer.py" -Destination ".\scripts\profile_optimizer.py" -Force
Copy-Item ".\fpl-oracle-phase10\tests\test_pipeline_integration.py" -Destination ".\tests\test_pipeline_integration.py" -Force
Copy-Item ".\fpl-oracle-phase10\PHASE_10_README.md" -Destination ".\PHASE_10_README.md" -Force
Remove-Item ".\fpl-oracle-phase10\" -Recurse -Force

python -m pytest tests\ -q
uvicorn app.main:app --reload
```

## Where this leaves the project

All 10 planned phases are now delivered:

1. Project setup and architecture
2. Database and models
3. FPL API integration
4. Data collection and storage
5. Prediction engine (DELPHI)
6. Transfer optimization (OR-Tools)
7. Squad management
8. Dashboard (Neo-Brutalism)
9. Weekly reporting + scheduling
10. Integration testing, performance profiling, and pipeline hardening

The realistic remaining work before a full season is operational, not
architectural: running `scripts.sync_squad` once your season starts and
the FPL API returns real picks for `FPL_TEAM_ID` (the 404s in your log
are expected before the season begins), keeping an eye on
`profile_optimizer.py`'s numbers as the real player pool fills in, and
optionally wiring a real Telegram/Discord channel (the documented stubs
in `app/services/reporting/delivery.py`) whenever you want.
