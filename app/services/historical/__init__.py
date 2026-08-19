"""Phase 12: historical season pretraining package.

Pulls prior-season gameweek data from the community-maintained
vaastav/Fantasy-Premier-League GitHub repo, matches it to current
`Player` rows where possible, and persists it into
`HistoricalPlayerGameweekStats` so `ModelTrainingService` can pretrain
DELPHI on far more data than a single in-progress season provides.

Entirely separate from the live-FPL-API ingestion path
(`app.services.ingestion`) and never called by the weekly pipeline or
scheduler - this is a manual, occasional CLI operation (see
`scripts/sync_historical_data.py`), since past seasons never change.
"""

from app.services.historical.service import (
    HistoricalIngestionResult,
    HistoricalIngestionService,
)

__all__ = [
    "HistoricalIngestionResult",
    "HistoricalIngestionService",
]
