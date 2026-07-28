"""
FPL API client configuration.

MERGE NOTE
----------
This file is deliberately self-contained so it can run standalone in
this Phase 3 delivery. In your actual project, fold ``FPLAPISettings``
into your existing ``app/core/config.py`` ``Settings`` class instead
of importing a second settings object — e.g.:

    class Settings(BaseSettings):
        ...your existing fields...

        fpl_request_timeout_seconds: float = 10.0
        fpl_max_retries: int = 3
        fpl_retry_backoff_seconds: float = 1.0
        fpl_max_concurrent_requests: int = 5
        fpl_user_agent: str = "fpl-oracle-ai/0.1 (+personal project)"

All values are overridable via environment variables / your .env file
(e.g. ``FPL_MAX_RETRIES=5``), consistent with your existing config
approach.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class FPLAPISettings(BaseSettings):
    """Tunables for talking to the official FPL API politely and reliably."""

    model_config = SettingsConfigDict(env_prefix="FPL_", env_file=".env", extra="ignore")

    request_timeout_seconds: float = 10.0
    """Per-request timeout. The FPL API is usually fast, but occasionally
    slow to a crawl during deadline-time traffic spikes."""

    max_retries: int = 3
    """Retries for transient failures (timeouts, connection errors, 5xx,
    429). Non-transient errors (404, validation failures) are never
    retried — retrying won't fix a bad player ID."""

    retry_backoff_seconds: float = 1.0
    """Base delay for exponential backoff between retries (1s, 2s, 4s, ...)."""

    max_concurrent_requests: int = 5
    """Cap on simultaneous outbound requests, used when fetching
    element-summary for many players at once. Keeps us from hammering
    the FPL API and getting (soft) rate-limited."""

    user_agent: str = "fpl-oracle-ai/0.1 (+personal project; contact via GitHub)"
    """FPL has no public API terms, but identifying our client honestly
    is good citizenship and makes debugging easier on our end too."""


fpl_api_settings = FPLAPISettings()
