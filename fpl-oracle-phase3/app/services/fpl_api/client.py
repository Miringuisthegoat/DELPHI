"""
Async client for the official Fantasy Premier League API.

Design goals
------------
1. **Everything downstream sees typed Pydantic models**, never raw
   dicts — parsing/validation happens once, here, at the boundary.
2. **Everything downstream sees ``FPLAPIError`` subclasses**, never
   raw ``httpx`` exceptions — see ``exceptions.py`` for why.
3. **Resilient by default**: transient failures (timeouts, connection
   resets, 5xx, 429) are retried with exponential backoff; permanent
   failures (404, malformed payload) fail fast, since retrying won't
   help.
4. **Polite to the FPL API**: a single shared ``httpx.AsyncClient``
   (connection pooling) and a semaphore capping concurrent requests,
   since ``element-summary`` needs to be called once per player of
   interest rather than once for the whole squad.

This client only *fetches and parses*. Persisting results into the
database is Phase 4's job (a ``DataIngestionService`` that takes
these typed models and upserts them into the Players / Teams /
Fixtures / PlayerStatistics tables) — kept separate so the client
stays testable in isolation and reusable if the storage layer changes.
"""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.fpl_settings import FPLAPISettings, fpl_api_settings
from app.schemas.fpl_bootstrap import FPLBootstrapStatic
from app.schemas.fpl_element_summary import FPLElementSummary
from app.schemas.fpl_fixtures import FPLFixture
from app.schemas.fpl_live import FPLEventLive
from app.services.fpl_api.endpoints import (
    BOOTSTRAP_STATIC,
    ELEMENT_SUMMARY,
    ENTRY,
    ENTRY_EVENT_PICKS,
    EVENT_LIVE,
    FIXTURES,
    FPL_BASE_URL,
)
from app.services.fpl_api.exceptions import (
    FPLAPIError,
    FPLConnectionError,
    FPLNotFoundError,
    FPLRateLimitedError,
    FPLResponseParsingError,
    FPLServerError,
    FPLTimeoutError,
)

logger = logging.getLogger(__name__)

_RETRYABLE_EXCEPTIONS = (FPLConnectionError, FPLTimeoutError, FPLRateLimitedError, FPLServerError)


class FPLAPIClient:
    """Thin, typed, resilient wrapper around the official FPL API.

    Use as an async context manager so the underlying connection pool
    is always closed cleanly:

        async with FPLAPIClient() as client:
            bootstrap = await client.get_bootstrap_static()
            fixtures = await client.get_fixtures(event=8)

    Or, if you're managing the lifecycle yourself (e.g. one long-lived
    client for a scheduled job that runs many operations):

        client = FPLAPIClient()
        try:
            ...
        finally:
            await client.close()
    """

    def __init__(self, settings: FPLAPISettings | None = None) -> None:
        self._settings = settings or fpl_api_settings
        self._client = httpx.AsyncClient(
            base_url=FPL_BASE_URL,
            timeout=self._settings.request_timeout_seconds,
            headers={"User-Agent": self._settings.user_agent},
        )
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrent_requests)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying connection pool. Safe to call multiple times."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public endpoint methods
    # ------------------------------------------------------------------

    async def get_bootstrap_static(self) -> FPLBootstrapStatic:
        """Fetch the full bootstrap-static payload: all players, teams,
        gameweeks, and game settings.

        This is the heaviest single endpoint (multiple MB of JSON) and
        should typically be called once per sync cycle, not once per
        player.
        """
        data = await self._get_json(BOOTSTRAP_STATIC)
        return self._parse(FPLBootstrapStatic, data, context="bootstrap-static")

    async def get_fixtures(self, event: int | None = None) -> list[FPLFixture]:
        """Fetch fixtures, optionally filtered to a single gameweek.

        Args:
            event: If given, only fixtures for this gameweek are
                returned. If omitted, the full season's fixture list
                is returned (useful for building the fixture-difficulty
                ticker several gameweeks ahead).
        """
        params = {"event": event} if event is not None else None
        data = await self._get_json(FIXTURES, params=params)
        return [
            self._parse(FPLFixture, item, context=f"fixtures[{i}]")
            for i, item in enumerate(data)
        ]

    async def get_element_summary(self, element_id: int) -> FPLElementSummary:
        """Fetch one player's full history + upcoming fixtures.

        Args:
            element_id: The FPL player ("element") ID, as found in
                ``FPLBootstrapStatic.elements[i].id``.

        Raises:
            FPLNotFoundError: If ``element_id`` doesn't correspond to
                a real player.
        """
        path = ELEMENT_SUMMARY.format(element_id=element_id)
        data = await self._get_json(path)
        return self._parse(
            FPLElementSummary, data, context=f"element-summary/{element_id}"
        )

    async def get_element_summaries_bulk(
        self, element_ids: list[int]
    ) -> dict[int, FPLElementSummary | FPLAPIError]:
        """Fetch element-summary for many players concurrently, capped
        by ``max_concurrent_requests``.

        Rather than letting one player's failure (e.g. a stale ID)
        abort the whole batch, each result is either the parsed
        summary or the ``FPLAPIError`` raised while fetching it — the
        caller (Phase 4's ingestion job) decides whether a handful of
        per-player failures should block the rest of the sync.

        Returns:
            A dict keyed by ``element_id``, preserving input order is
            not guaranteed but every input id is guaranteed a key.
        """

        async def _fetch_one(element_id: int) -> tuple[int, FPLElementSummary | FPLAPIError]:
            async with self._semaphore:
                try:
                    summary = await self.get_element_summary(element_id)
                    return element_id, summary
                except FPLAPIError as exc:
                    logger.warning(
                        "Failed to fetch element-summary for player %s: %s",
                        element_id,
                        exc,
                    )
                    return element_id, exc

        results = await asyncio.gather(*(_fetch_one(eid) for eid in element_ids))
        return dict(results)

    async def get_event_live(self, event_id: int) -> FPLEventLive:
        """Fetch live/completed stats for every player in a gameweek.

        Args:
            event_id: The gameweek number.
        """
        path = EVENT_LIVE.format(event_id=event_id)
        data = await self._get_json(path)
        return self._parse(FPLEventLive, data, context=f"event/{event_id}/live")

    async def get_entry_event_picks(
        self, entry_id: int, event_id: int
    ) -> dict[str, Any]:
        """Fetch a specific manager's squad picks for a gameweek.

        Returned as a raw dict (rather than a Pydantic model) for now
        since "My Squad" sync — mapping this onto our own squad state
        rather than just the global player pool — is more naturally
        scoped as a Phase 4/7 concern. The typed client method exists
        here so the endpoint is already wired and tested.
        """
        path = ENTRY_EVENT_PICKS.format(entry_id=entry_id, event_id=event_id)
        return await self._get_json(path)

    async def get_entry(self, entry_id: int) -> dict[str, Any]:
        """Fetch a specific manager's overall summary (team name,
        overall rank, total points, etc.)."""
        path = ENTRY.format(entry_id=entry_id)
        return await self._get_json(path)

    # ------------------------------------------------------------------
    # Internal HTTP plumbing
    # ------------------------------------------------------------------

    async def _get_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Perform a single GET request (with retries) and return decoded JSON.

        Retry parameters come from ``self._settings`` — built fresh per
        instance via ``AsyncRetrying`` rather than a static ``@retry``
        decorator — so each ``FPLAPIClient`` instance honours whatever
        settings it was constructed with (important for tests, which
        use near-zero backoff, and for any future caller that wants a
        more/less aggressive retry policy for a specific job).
        """
        retrying = AsyncRetrying(
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            stop=stop_after_attempt(self._settings.max_retries + 1),
            wait=wait_exponential(multiplier=self._settings.retry_backoff_seconds, max=30),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await self._get_json_once(path, params)

    async def _get_json_once(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Perform exactly one GET request and return decoded JSON,
        translating every failure mode into a specific ``FPLAPIError``
        subclass. No retry logic lives here — see ``_get_json``.
        """
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            logger.warning("FPL API request timed out: %s", path)
            raise FPLTimeoutError(f"Timed out calling {path}") from exc
        except httpx.ConnectError as exc:
            logger.warning("FPL API connection failed: %s (%s)", path, exc)
            raise FPLConnectionError(f"Could not connect to {path}") from exc
        except httpx.HTTPError as exc:
            logger.warning("FPL API request failed: %s (%s)", path, exc)
            raise FPLConnectionError(f"Request to {path} failed: {exc}") from exc

        if response.status_code == 404:
            raise FPLNotFoundError(f"FPL API returned 404 for {path}")
        if response.status_code == 429:
            raise FPLRateLimitedError(f"FPL API rate-limited request to {path}")
        if 500 <= response.status_code < 600:
            raise FPLServerError(
                response.status_code,
                f"FPL API returned {response.status_code} for {path}",
            )
        if response.status_code >= 400:
            # Any other 4xx is treated as permanent (bad request on our
            # side) and is deliberately NOT retried.
            raise FPLAPIError(
                f"FPL API returned unexpected status {response.status_code} for {path}: "
                f"{response.text[:200]}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise FPLResponseParsingError(
                f"FPL API response for {path} was not valid JSON"
            ) from exc

    @staticmethod
    def _parse(model: type[Any], data: Any, *, context: str) -> Any:
        """Validate raw JSON against a Pydantic model, wrapping failures
        in ``FPLResponseParsingError`` with useful context.
        """
        try:
            return model.model_validate(data)
        except Exception as exc:  # Pydantic's ValidationError, primarily
            logger.error("Failed to parse FPL API response for %s: %s", context, exc)
            raise FPLResponseParsingError(
                f"Response for {context} did not match expected schema: {exc}"
            ) from exc
