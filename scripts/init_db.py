"""One-off script to create the database schema.

Usage:
    python -m scripts.init_db
"""

from __future__ import annotations

from loguru import logger

from app.core.logging import configure_logging
from app.db.session import init_db


def main() -> None:
    """Create all tables that do not yet exist."""
    configure_logging()
    init_db()
    logger.info("Database initialised successfully.")


if __name__ == "__main__":
    main()