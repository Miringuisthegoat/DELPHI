# Phase 8 — Dashboard (Neo-Brutalism)

## What this delivers

A FastAPI + Jinja2 + custom-CSS dashboard (no Streamlit — per your spec,
Neo-Brutalism needs real CSS control) that surfaces everything Phases
5–7 have built: DELPHI predictions, the Phase 6 transfer optimizer, and
your live Phase 7 squad state, all on one page at `GET /dashboard`.

```
app/
├── services/
│   └── dashboard/                       # NEW package
│       ├── __init__.py
│       └── service.py                   # DashboardService — the view-model builder
├── web/                                  # NEW package (HTML routes, distinct from app/api's JSON routes)
│   ├── __init__.py
│   └── routes.py                        # GET /dashboard
├── templates/                             # NEW — Jinja2 templates
│   ├── base.html
│   └── dashboard.html
├── static/
│   └── css/
│       └── style.css                     # NEW — Neo-Brutalism stylesheet
└── main.py                               # MODIFIED — mounts /static, registers the dashboard router
tests/
└── test_dashboard_service.py             # NEW — 8 tests
requirements.txt                          # MODIFIED — adds jinja2, python-multipart
```

All 8 new tests pass, verified against the real `TransferOptimizerService`
(OR-Tools) and a real Jinja2 render of `dashboard.html` — not just the
Python view-model.

## Design decisions worth knowing about

**One `DashboardService`, thin route.** `app/web/routes.py` does almost
nothing: resolve which gameweek to show, open a session, call
`DashboardService.build_view()`, render. Every actual query — squad
lookup, joining `Player`/`Team` onto `SquadPlayer`, pulling horizon-1
`Prediction` rows, calling `TransferOptimizerService`, building the
fixture ticker — lives in the service layer, matching every other
`*Service` in this project (stateless aside from the `Session` passed
per call).

**Never lets a missing dependency 500 the page.** Two states are treated
as normal, not exceptional, because they're the first thing you'll hit
in a fresh season:
- No `SquadState` synced yet → the whole page renders an explanatory
  empty-state box instead of crashing, pointing you at
  `POST /api/v1/squad/sync/{gameweek}`.
- Squad exists but no `Prediction` rows for that gameweek yet →
  projected points/captain/transfer-suggestion panels each show their
  own empty state pointing at `POST /api/v1/predictions/generate/{gameweek}`,
  while the squad table, injury alerts, and fixture ticker (which don't
  need predictions) still render normally.
- `TransferOptimizerService.optimize()` raising `OptimizationError` (e.g.
  no feasible transfer combination) is caught and shown as a message,
  never a 500.

**Captain/vice-captain are derived, not stored.** Phase 5's `Prediction`
table has no "is this the captain" concept — it's just per-player/
gameweek/horizon predicted points. The dashboard picks the *starting*
squad player with the highest horizon-1 prediction as captain (exactly
what "who should I captain" means in FPL) and the runner-up as vice.
Projected points sum every starter once, then add the captain's
prediction a second time (FPL's own doubling rule) — bench players never
count, verified by a dedicated test.

**"At or before" gameweek resolution, matching Phase 6/7's own
convention.** `/dashboard` defaults to the most recently *synced* squad
gameweek (not necessarily gameweek 1), and internally resolves "the
squad for gameweek N" as the latest `SquadState` at or before N — a
snapshot stays valid until the next sync, exactly like
`TransferOptimizerService._load_squad_state`.

**Neo-Brutalism is real CSS, not a theme name.** `style.css` uses your
exact palette (pink/cyan/green/orange/yellow, black borders/text), hard
un-blurred offset drop-shadows on every panel/card/button, chunky
uppercase display type, and zero gradients — flat color blocks only. A
tiny hover/active state on buttons (shadow collapsing as the button
"presses down") is the one bit of motion, reinforcing the physical/
tactile feel rather than a soft/glassy one.

**Injury/availability rows are visually flagged inline** (a soft red
tint on the table row) as well as pulled into their own "Injury /
Availability Alerts" panel — so a doubtful player is impossible to miss
whether you're scanning the full squad or just the alerts.

**Fixture ticker only shows teams you actually own players from.**
Pulled from `Fixture` rows for the next 5 gameweeks, joined against your
squad's distinct `team_id`s — not all 20 Premier League clubs — since
the only fixtures relevant to a weekly decision are your own players'.

## New dependencies

```
jinja2==3.1.5
python-multipart==0.0.20
```

(`python-multipart` isn't strictly required yet, but FastAPI's own docs
recommend it once Jinja2Templates + any future HTML form on this router
are in play — cheap to add now.)

## Running it

```powershell
uvicorn app.main:app --reload
```

Then visit:

```
http://127.0.0.1:8000/dashboard
http://127.0.0.1:8000/dashboard?gameweek=8
```

The gameweek input at the top of the page re-submits via `GET`, so you
can jump between weeks without touching the URL bar.

## Tests

```powershell
pytest tests\test_dashboard_service.py -v
```

8 tests covering: no-squad empty state, squad-without-predictions empty
state, captain selection (highest predicted starter), projected-points
doubling for the captain, bench players correctly excluded from
projected points, injury alerts, the fixture ticker, and the "at or
before gameweek" squad-state fallback.

I additionally verified (outside the committed test suite, since it
needs the full dependency stack) that `dashboard.html` renders correctly
end-to-end against a real `TransferOptimizerService` solve and against
both empty-state paths — no Jinja2 errors, no missing-context issues.

## Merging into your existing project

```powershell
Copy-Item ".\fpl-oracle-phase8\app\services\dashboard" -Destination ".\app\services\dashboard" -Recurse -Force
Copy-Item ".\fpl-oracle-phase8\app\web" -Destination ".\app\web" -Recurse -Force
Copy-Item ".\fpl-oracle-phase8\app\templates" -Destination ".\app\templates" -Recurse -Force
Copy-Item ".\fpl-oracle-phase8\app\static" -Destination ".\app\static" -Recurse -Force
Copy-Item ".\fpl-oracle-phase8\app\main.py" -Destination ".\app\main.py" -Force
Copy-Item ".\fpl-oracle-phase8\tests\test_dashboard_service.py" -Destination ".\tests\test_dashboard_service.py" -Force
Copy-Item ".\fpl-oracle-phase8\requirements.txt" -Destination ".\requirements.txt" -Force
Copy-Item ".\fpl-oracle-phase8\PHASE_8_README.md" -Destination ".\PHASE_8_README.md" -Force
Remove-Item ".\fpl-oracle-phase8\" -Recurse -Force

pip install -r requirements.txt --break-system-packages
python -m pytest tests\ -q
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000/dashboard`.

## What's deliberately *not* in this phase

- No auth/session — this is a single-user personal tool, matching the
  rest of the project so far.
- No client-side JS/charts (e.g. historical prediction-error graphs from
  the project brief's "Dashboard" wishlist) — the gameweek switcher is a
  plain HTML form, no fetch/AJAX. Charting historical accuracy needs
  Phase 5's `Prediction.actual_points`/`prediction_error` data across many
  gameweeks, which is thin until more of the season is played; flag if
  you want a first pass now anyway (e.g. a simple SVG sparkline with no
  JS charting library).
- No mobile-specific layout beyond a basic responsive breakpoint at
  640px — functional on phones, not specifically optimized.

## Next phase

Phase 9 (Weekly Reporting) turns this same `DashboardView` data (plus
DELPHI's explainability strings) into a standalone prose report per
gameweek, with APScheduler wiring for automatic weekly generation and a
stub for future Telegram/Discord delivery.
