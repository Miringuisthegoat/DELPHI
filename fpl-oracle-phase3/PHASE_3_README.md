# Phase 3 — FPL API Integration

## What this delivers

A typed, resilient async client for the official Fantasy Premier League
API, plus FastAPI routes to exercise it manually. This phase deliberately
stops at **fetch + validate** — it does not write to your database.
Persisting into `Players` / `Teams` / `Fixtures` / `PlayerStatistics` is
Phase 4 (Data Collection & Storage), which will consume the typed models
built here.

## Files, and where they go in your existing tree

```
app/
├── core/
│   └── fpl_settings.py                # NEW — see merge note below
├── schemas/
│   ├── fpl_bootstrap.py                # NEW
│   ├── fpl_fixtures.py                 # NEW
│   ├── fpl_element_summary.py          # NEW
│   └── fpl_live.py                     # NEW
├── services/
│   └── fpl_api/                        # NEW package
│       ├── __init__.py
│       ├── client.py                   # the client itself
│       ├── endpoints.py                # URL registry
│       └── exceptions.py               # error hierarchy
└── api/
    └── v1/
        └── endpoints/
            └── fpl.py                  # NEW — diagnostic routes
tests/
└── test_fpl_api_client.py              # NEW
```

### 1. Config merge

`app/core/fpl_settings.py` is self-contained so it drops in and works
immediately, but it's cleaner long-term to fold its fields into your
existing `Settings` class in `app/core/config.py` (see the module
docstring for the exact fields to add) and delete the standalone file.
Either way works — nothing else in this delivery cares which one you use.

### 2. Wire the router

Your project doesn't have a router-aggregator file (e.g. `app/api/v1/api.py`)
— `app/main.py` wires routers directly onto `app`. So add this in
`app/main.py`, anywhere after `app = FastAPI(...)` is defined:

```python
from app.api.v1.endpoints import fpl

app.include_router(fpl.router, prefix="/api/v1/fpl", tags=["fpl-integration"])
```

(If you later introduce an aggregator file as the project grows, this
line just moves there unchanged — `fpl.router` itself doesn't care who
includes it.)

### 3. New dependencies

Add to `requirements.txt`:

```
httpx>=0.27
tenacity>=8.2
pydantic-settings>=2.0
respx>=0.20          # test-only
pytest-asyncio>=0.23 # test-only, if not already present
```

## Design decisions worth knowing about

- **One client, five endpoints.** `FPLAPIClient` wraps `bootstrap-static`,
  `fixtures`, `element-summary/{id}`, `event/{id}/live`, and the manager
  (`entry`) endpoints needed later for syncing *your* squad specifically.
- **Typed everywhere.** Every response is validated into a Pydantic model
  before it leaves the client. Field names deliberately mirror FPL's own
  raw naming (`now_cost`, `element_type`, etc.) — renaming to
  domain-friendly names happens in Phase 4's ingestion service, not here,
  so this layer stays a faithful, low-maintenance mirror of the real API.
- **Errors are ours, not httpx's.** Every failure mode — timeout,
  connection error, 429, 5xx, 404, schema mismatch — is translated into a
  specific `FPLAPIError` subclass (`exceptions.py`). Callers never need to
  import or catch `httpx` exceptions.
- **Retries are automatic and targeted.** Timeouts, connection errors,
  429s, and 5xx responses are retried with exponential backoff (default:
  3 retries, starting at 1s). 404s and malformed payloads are **not**
  retried — retrying a bad player ID or a broken schema assumption just
  wastes time and hides a real problem.
- **Bulk player fetching is capped and fault-isolated.**
  `get_element_summaries_bulk()` fetches many players concurrently
  (default cap: 5 at once, to stay polite to FPL's servers) and returns a
  per-player result that's either the parsed summary or the specific
  error — one stale player ID won't abort a 30-player batch.
- **extra="ignore" on every schema.** FPL can add new fields to its API
  at any time; our models simply ignore fields we don't use yet. If FPL
  *removes or renames* something we depend on, Pydantic validation fails
  loudly — that's `FPLResponseParsingError`, a deliberately distinct error
  from "FPL is down," since it means our code (not the network) is stale.

## Testing

All 7 tests run fully offline against mocked HTTP responses (via `respx`)
— they don't hit the real FPL API, so they're deterministic and won't
break due to FPL downtime or throttling.

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest tests/test_fpl_api_client.py -v
```

Coverage: successful parsing, 404 handling, malformed-response handling,
transient-error retry-then-succeed, retry exhaustion, gameweek-filtered
fixtures, and bulk-fetch fault isolation.

## Manually verifying against the real API

Since this sandbox's network doesn't have access to
`fantasy.premierleague.com`, I built and tested this entirely against
mocked responses. Once you drop these files into your project, a quick
real-world smoke test:

```bash
uvicorn app.main:app --reload
curl http://127.0.0.1:8000/api/v1/fpl/bootstrap-static | head -c 500
curl "http://127.0.0.1:8000/api/v1/fpl/fixtures?event=1"
curl http://127.0.0.1:8000/api/v1/fpl/players/328/summary
```

(Adjust the `/api/v1` prefix to match wherever your `api_router` is
mounted in `main.py`.)

## A note on error handling alignment

Your `app/main.py` has a global exception handler for `FplOracleError`
(and subclasses) that returns a consistent `{"error": ..., "detail": ...}`
JSON shape. `FPLAPIError` in this delivery does **not** inherit from
`FplOracleError` — it's a standalone hierarchy, since it represents
*external API* failures (FPL being down, a schema change) rather than
your application's own domain errors. `app/api/v1/endpoints/fpl.py`
catches it manually and raises `HTTPException` instead, which keeps the
distinction visible in the response (502 for "FPL is unreachable" vs.
400 for "your own domain rule was violated").

If you'd rather have one unified error shape across the whole app, make
`FPLAPIError` extend `FplOracleError` and delete the manual `try/except`
blocks in the router — your existing global handler will catch it
automatically. Happy to make that change if you'd prefer it; just say so.

## What's deliberately *not* in Phase 3

- Writing any of this to the database (Phase 4).
- Mapping FPL's raw player/team fields onto your `Players`/`Teams` ORM
  models (Phase 4).
- Scheduling automatic weekly syncs via APScheduler (also Phase 4, or
  later — your existing `scheduler/` module is untouched by this delivery).

## Next: Phase 4 preview

Phase 4 will add a `DataIngestionService` that takes the typed models
from this client and upserts them into your `Players`, `Teams`,
`Fixtures`, and `PlayerStatistics` tables — handling price-change
detection, new-player detection, and incremental gameweek stat updates,
using your existing `session_scope()` pattern.
