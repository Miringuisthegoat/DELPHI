"""FPL API integration package: HTTP client, endpoint registry, and exceptions."""

from app.services.fpl_api.client import FPLAPIClient
from app.services.fpl_api.exceptions import (
    FPLAPIError,
    FPLConnectionError,
    FPLNotFoundError,
    FPLRateLimitedError,
    FPLResponseParsingError,
    FPLServerError,
    FPLTimeoutError,
)

__all__ = [
    "FPLAPIClient",
    "FPLAPIError",
    "FPLConnectionError",
    "FPLNotFoundError",
    "FPLRateLimitedError",
    "FPLResponseParsingError",
    "FPLServerError",
    "FPLTimeoutError",
]
