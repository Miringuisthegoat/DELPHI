"""
Application-wide exception hierarchy.

Using specific exception types (rather than bare Exception/ValueError
everywhere) lets callers catch precisely what they expect and lets the
FastAPI layer translate domain errors into clean HTTP responses.
"""


class FplOracleError(Exception):
    """Base class for all application-specific errors."""


class ConfigurationError(FplOracleError):
    """Raised when required configuration is missing or invalid."""


class FplApiError(FplOracleError):
    """Raised when the official FPL API returns an error or unexpected payload."""


class DataIngestionError(FplOracleError):
    """Raised when downloaded data cannot be parsed or persisted."""


class SquadRuleViolation(FplOracleError):
    """Raised when a proposed squad/transfer violates official FPL rules."""


class OptimizationError(FplOracleError):
    """Raised when the transfer/lineup optimizer fails to find a valid solution."""


class PredictionError(FplOracleError):
    """Raised when the ML prediction pipeline fails."""


class RecordNotFoundError(FplOracleError):
    """Raised when a requested database record does not exist."""
