"""
Centralized logging setup.

Uses loguru for structured, readable logs with automatic rotation.
Call `configure_logging()` once at application startup (done in app/main.py).
Anywhere else in the codebase, just `from loguru import logger`.
"""

import sys

from loguru import logger

from app.core.config import settings


def configure_logging() -> None:
    """Configure loguru sinks: colored console output + rotating file logs."""
    logger.remove()  # drop the default handler so we control formatting

    logger.add(
        sys.stderr,
        level=settings.log_level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )

    logger.add(
        settings.log_dir / "fpl_oracle.log",
        level=settings.log_level,
        rotation="1 week",
        retention="8 weeks",
        compression="zip",
        enqueue=True,  # process-safe writes
        backtrace=False,
        diagnose=False,
    )

    logger.info(f"Logging configured for env='{settings.app_env}' level='{settings.log_level}'")
