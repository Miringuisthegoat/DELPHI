# Phase 9 — Weekly Reporting

## What this delivers

`WeeklyReportService`: turns everything Phases 5–8 already know (DELPHI
predictions, the Phase 6 optimizer, Phase 7's live squad state, Phase 8's
`DashboardView`) into a single, readable **prose** report for one
gameweek — plus APScheduler wiring so it can generate automatically, and
a pluggable delivery-channel interface with a working console channel
today and documented Telegram/Discord stubs for later.

```
app/
├── services/
│   └── reporting/                        # NEW package
│       ├── __init__.py
│       ├── service.py                     # WeeklyReportService, WeeklyReport
│       └── delivery.py                    # DeliveryChannel + Console/Telegram/Discord
├── scheduler/
│   └── jobs.py                            # NEW — start_scheduler()/stop_scheduler()
├── api/
│   └── v1/
│       └── endpoints/
│           └── reports.py                 # NEW — GET /{gw}, POST /{gw}/send
└── main.py                                # MODIFIED — registers reports router + scheduler lifecycle
scripts/
└── generate_report.py                     # NEW — CLI: python -m scripts.generate_report
tests/
└── test_reporting_service.py              # NEW — 9 tests
```

No new dependencies — `apscheduler` has been in `requirements.txt` since
Phase 1, just unused until now.

## Design decisions worth knowing about

**A formatter over Phase 8's view model, not a second data-access
layer.** `WeeklyReportService.build_report()` calls
`DashboardService.build_view()` and turns the result into prose. Every
number a report needs (projected points, captain, transfer suggestion,
squad snapshot, injury alerts) is already computed there — duplicating
those queries here would create two places that could disagree about
"what DELPHI currently recommends." This also means the report and the
dashboard can never show different numbers for the same gameweek.

**Explainability lives in prose, matching Phase 5's "never output a bare
number" rule.** The Captaincy and Transfer Suggestion sections quote the
existing `reasoning` strings Phase 5/6 already generate; nothing in the
report is a raw number with no sentence around it.

**Sections that would be misleading are omitted, not stubbed out.** No
squad synced yet → one explanatory section, nothing else. No predictions
generated yet → the Projected Points / Captaincy / Transfer sections are
replaced by one note pointing at `POST /api/v1/predictions/generate/{gw}`,
while the Squad Snapshot (which needs neither) still renders. A
Prediction Accuracy section only appears once the *previous* gameweek
has actually been evaluated (`Prediction.actual_points` populated via
`evaluate_gameweek`) — early in a season there's nothing genuine to
report on DELPHI's learning progress, so the section simply doesn't
exist rather than showing a misleading placeholder.

**One report object, two renderings, always in sync.** `WeeklyReport`
stores a list of `ReportSection`s once; `to_markdown()` and
`to_plain_text()` both render from that same list, so a Telegram/Discord
delivery (plain text) can never drift out of sync with the
markdown/API/dashboard rendering.

**Delivery is a real interface today, not just a TODO comment.**
`DeliveryChannel` is an ABC with one method (`send(report) -> DeliveryResult`).
`ConsoleDeliveryChannel` actually works right now (logs via loguru — genuinely
useful for the scheduled job and for testing this phase with zero external
account setup). `TelegramDeliveryChannel` / `DiscordDeliveryChannel` are
documented stubs that raise `NotImplementedError` with the exact settings
and HTTP call a future phase needs to add — they fail loudly rather than
silently no-op'ing if someone calls them before they're wired up.

**The scheduled job is deliberately read-only.** `generate_and_deliver_weekly_report()`
(in `app/scheduler/jobs.py`) builds and delivers a report from whatever
data already exists — it does *not* trigger a fresh FPL sync or
re-generate predictions itself. Chaining "sync → predict → optimize →
report" into one cron job is a natural follow-up, but bundling it here
would mean one slow/failing step (e.g. the FPL API being down at 8am
Tuesday) silently breaks reporting too. This keeps the job safe to
enable immediately via the `ENABLE_SCHEDULER`/`WEEKLY_UPDATE_CRON`
settings that have existed since Phase 1.

**Gameweek resolution matches Phase 8's own convention.** The scheduled
job reports on the latest gameweek with a synced `SquadState` — the same
"at or before" fallback `DashboardService`/`TransferOptimizerService`
already use — rather than needing a fresh API call just to ask FPL what
the "current" gameweek is.

## Running it

```powershell
# Print this gameweek's report to the console:
python -m scripts.generate_report --gameweek 8

# Plain-text (Telegram/Discord-style) instead of markdown:
python -m scripts.generate_report --gameweek 8 --format text

# Also "deliver" it (console channel logs it):
python -m scripts.generate_report --gameweek 8 --send
```

Or via the API (`uvicorn app.main:app --reload`):

```
GET  /api/v1/reports/8                 (markdown)
GET  /api/v1/reports/8?format=text     (plain text)
POST /api/v1/reports/8/send            (delivers via the console channel)
```

### Automatic weekly generation

Already-existing `.env` settings control this — nothing new to add:

```
ENABLE_SCHEDULER=true
WEEKLY_UPDATE_CRON=0 8 * * 2   # Tuesdays 08:00 UTC, adjust to taste
```

With `ENABLE_SCHEDULER=true`, starting the app (`uvicorn app.main:app`)
now also starts a background APScheduler job that builds and
console-delivers the weekly report on that cron schedule, and shuts it
down cleanly on app shutdown.

## Tests

```powershell
pytest tests\test_reporting_service.py -v
```

9 tests covering: no-squad empty section, squad-without-predictions
section set, full report with projection/captaincy/transfer sections,
markdown + plain-text rendering, injury alerts, the accuracy section
being omitted when nothing's been evaluated yet, the accuracy section
appearing once it has, the console channel delivering successfully, and
the Telegram stub raising `NotImplementedError` as documented.

## Merging into your existing project

```powershell
Copy-Item ".\fpl-oracle-phase9\app\services\reporting" -Destination ".\app\services\reporting" -Recurse -Force
Copy-Item ".\fpl-oracle-phase9\app\scheduler\jobs.py" -Destination ".\app\scheduler\jobs.py" -Force
Copy-Item ".\fpl-oracle-phase9\app\api\v1\endpoints\reports.py" -Destination ".\app\api\v1\endpoints\reports.py" -Force
Copy-Item ".\fpl-oracle-phase9\app\main.py" -Destination ".\app\main.py" -Force
Copy-Item ".\fpl-oracle-phase9\scripts\generate_report.py" -Destination ".\scripts\generate_report.py" -Force
Copy-Item ".\fpl-oracle-phase9\tests\test_reporting_service.py" -Destination ".\tests\test_reporting_service.py" -Force
Copy-Item ".\fpl-oracle-phase9\PHASE_9_README.md" -Destination ".\PHASE_9_README.md" -Force
Remove-Item ".\fpl-oracle-phase9\" -Recurse -Force

python -m pytest tests\ -q
uvicorn app.main:app --reload
```

Then try `http://127.0.0.1:8000/api/v1/reports/1` (or whatever gameweek
you've synced/predicted for).

## What's deliberately *not* in this phase

- **Telegram/Discord delivery itself** — the interface and documented
  stubs are here, but actually sending a message needs a bot
  token/webhook URL you'd have to create and add to `.env`. Flag if
  you'd like one wired up now; it's a small follow-up given the seam
  already exists (`app/services/reporting/delivery.py`).
- **Chaining sync → predict → optimize → report into one scheduled job**
  — the scheduler currently only reports on already-computed data (see
  design notes above). Bundling the whole weekly pipeline into one cron
  job is a reasonable Phase 10 hardening task.
- **Historical trend charts** (predicted vs. actual over many
  gameweeks) — the accuracy section reports on the single most-recent
  evaluated gameweek; a proper trend view wants more of the season
  played out first, plus a charting decision (SVG sparkline vs. a JS
  library) that's better scoped to Phase 8's dashboard than this phase.

## Next phase

Phase 10 (Integration/E2E Testing, Performance, Hardening) is the final
planned phase: wiring the full weekly pipeline together, profiling the
transfer optimizer under a fuller player pool, and general refactoring
before the first full season.
