# FPL Oracle AI

An AI-powered, season-long Fantasy Premier League management assistant.

This is **not** a one-shot team generator. It starts from your existing squad,
remembers every decision you make, learns from past prediction errors, and
recommends the highest-value action each gameweek while respecting all
official FPL rules.

## Status

🚧 **Phase 1 of 10: Project setup & architecture** — in progress.
See [Development Phases](#development-phases) below.

## Architecture

```
fpl-oracle/
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── core/                # Cross-cutting concerns
│   │   ├── config.py        #   Settings (env-driven, swap SQLite -> Postgres here)
│   │   ├── logging.py       #   Loguru setup
│   │   └── exceptions.py    #   Domain exception hierarchy
│   ├── db/                  # SQLAlchemy engine/session, migrations (Phase 2)
│   ├── models/              # SQLAlchemy ORM models (Phase 2)
│   ├── schemas/              # Pydantic request/response schemas
│   ├── services/             # Business logic (FPL client, squad service, etc.) (Phase 3+)
│   ├── ml/                   # Prediction models (Phase 5)
│   ├── optimization/         # OR-Tools transfer/lineup optimizer (Phase 6)
│   ├── scheduler/            # APScheduler jobs for weekly automation
│   └── api/                  # FastAPI routers
├── tests/                    # Pytest unit/integration tests
├── data/
│   ├── raw/                  # Raw downloaded FPL API payloads (cache/audit trail)
│   └── processed/            # Cleaned/derived datasets
├── logs/                     # Rotating application logs
├── scripts/                  # One-off / maintenance scripts (e.g. manual data pull)
├── requirements.txt
├── .env.example
└── README.md
```

### Design principles

- **Separation of concerns**: API layer (`app/api`) never talks to the database
  or the FPL API directly — it calls into `app/services`, which owns business
  logic and talks to `app/db` (persistence) and external clients.
- **Swap-friendly persistence**: `DATABASE_URL` in `.env` controls everything.
  Moving from SQLite to PostgreSQL later requires no code changes — just a
  connection string change (SQLAlchemy handles the dialect).
- **Swap-friendly ML**: prediction models live behind a common interface in
  `app/ml`, so Random Forest can be replaced/augmented with XGBoost or other
  models without touching the services that consume predictions.
- **Config via environment**: nothing environment-specific is hardcoded;
  everything flows through `app/core/config.py`.

## Getting started

```bash
# 1. Create and activate a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# edit .env and set FPL_TEAM_ID to your own team ID (found in the FPL site URL)

# 4. Run the API
uvicorn app.main:app --reload

# 5. Confirm it's alive
curl http://localhost:8000/health
```

## Development Phases

1. **Project setup and architecture** ← *we are here*
2. Database and models
3. FPL API integration
4. Data collection and storage
5. Prediction engine
6. Transfer optimization
7. Squad management
8. Dashboard (Streamlit)
9. Weekly reporting
10. Testing, refinement, and performance improvements

Each phase ends with a working, runnable increment plus tests before moving on.

## Running tests

```bash
pytest -v --cov=app
```
