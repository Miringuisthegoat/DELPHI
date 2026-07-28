"""Custom exception hierarchy for the FPL API integration layer.

Callers (data-sync jobs, API routes, the scheduler) should catch
``FPLAPIError`` rather than raw ``httpx`` exceptions, so that the rest
of the application doesn't need to know we're using httpx under the
hood. That keeps this module swappable later (e.g. for an aiohttp or
requests-based client) without touching any downstream code.
"""

from __future__ import annotations


class FPLAPIError(Exception):
    """Base class for all errors raised by the FPL API client."""


class FPLConnectionError(FPLAPIError):
    """Raised when the FPL API could not be reached at all.

    Covers DNS failures, connection refused/reset, and timeouts —
    i.e. we never got an HTTP response to inspect.
    """


class FPLTimeoutError(FPLConnectionError):
    """Raised when a request to the FPL API timed out."""


class FPLRateLimitedError(FPLAPIError):
    """Raised when the FPL API responds with HTTP 429 (Too Many Requests).

    The FPL API has no officially documented rate limit, but it does
    throttle aggressive clients. Callers should back off and retry
    later rather than hammering the endpoint.
    """


class FPLServerError(FPLAPIError):
    """Raised when the FPL API responds with a 5xx server error."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


class FPLNotFoundError(FPLAPIError):
    """Raised when the FPL API responds with 404 (e.g. an unknown
    player ID, gameweek, or manager entry ID)."""


class FPLResponseParsingError(FPLAPIError):
    """Raised when the FPL API returns a 2xx response, but the payload
    doesn't match the shape we expect (schema validation failure).

    This usually means FPL has changed the shape of their API and our
    Pydantic schemas need updating — it is deliberately a distinct
    error from connection/HTTP failures so it can be alerted on
    separately (it signals "our code is stale", not "the network/FPL
    is down").
    """
