"""
Phase 12: downloads and parses `merged_gw.csv` for a given season from
the vaastav/Fantasy-Premier-League GitHub repo.

Uses `httpx` directly (not `FPLAPIClient`) since this talks to raw
GitHub content, not the FPL API, and is a one-off/occasional pull rather
than something needing the FPL client's retry/backoff tuning for a live,
rate-limit-sensitive API. `raw.githubusercontent.com` is already in this
project's allowed egress domains.

Column drift across seasons (vaastav's CSV schema has changed slightly
over the years - xG/xA columns were added around 2021/22, some seasons
use `element` vs `id`, etc.) is handled by reading everything through
pandas with `usecols` omitted and letting `mappers.py` pull whatever
columns exist, defaulting absent ones - the same "extra=ignore, default
on missing" philosophy as this project's FPL API schemas.

HOTFIX: the repo's actual layout nests every season under a `data/`
directory (e.g. `.../master/data/2025-26/gws/merged_gw.csv`), not
directly off `master/` - confirmed against the live repo. The original
path template omitted `data/`, which meant every season 404'd
identically regardless of season string.
"""

from __future__ import annotations

import io
import logging

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

_RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master"
_MERGED_GW_PATH = "data/{season}/gws/merged_gw.csv"
_REQUEST_TIMEOUT_SECONDS = 30.0


class HistoricalFetchError(Exception):
    """Raised when a season's data can't be downloaded or parsed."""


class HistoricalDataFetcher:
    """Downloads one season's `merged_gw.csv` as a pandas DataFrame."""

    def __init__(self, timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    def fetch_season(self, season: str) -> pd.DataFrame:
        """Fetch and parse one season, e.g. `fetch_season("2023-24")`.

        Args:
            season: vaastav's folder naming, e.g. "2023-24", "2022-23".

        Raises:
            HistoricalFetchError: on any network, HTTP, or CSV-parsing
                failure - collected/logged by the caller (see
                `service.py`) rather than aborting a multi-season sync.
        """
        url = f"{_RAW_BASE}/{_MERGED_GW_PATH.format(season=season)}"
        try:
            response = httpx.get(url, timeout=self._timeout_seconds)
        except httpx.HTTPError as exc:
            raise HistoricalFetchError(
                f"Could not reach {url} for season {season}: {exc}"
            ) from exc

        if response.status_code == 404:
            raise HistoricalFetchError(
                f"No merged_gw.csv found for season {season} at {url} - "
                "check the season string matches vaastav's folder naming "
                "(e.g. '2023-24', not '2023-2024')."
            )
        if response.status_code >= 400:
            raise HistoricalFetchError(
                f"Unexpected status {response.status_code} fetching {url}"
            )

        try:
            df = pd.read_csv(io.StringIO(response.text))
        except Exception as exc:  # noqa: BLE001 - pandas raises several types
            raise HistoricalFetchError(
                f"Could not parse merged_gw.csv for season {season} as CSV: {exc}"
            ) from exc

        if df.empty:
            raise HistoricalFetchError(f"merged_gw.csv for season {season} was empty.")

        logger.info(
            "Fetched {} rows for season {} from vaastav/Fantasy-Premier-League".format(
                len(df), season
            )
        )
        return df
