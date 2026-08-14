"""One-off / cron-friendly script to generate (and optionally send) the
DELPHI weekly report.

Usage:
    python -m scripts.generate_report --gameweek 8
    python -m scripts.generate_report --gameweek 8 --send
    python -m scripts.generate_report --gameweek 8 --format text
"""

from __future__ import annotations

import argparse

from loguru import logger

from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.services.reporting import (
    ConsoleDeliveryChannel,
    TelegramDeliveryChannel,
    WeeklyReportService,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the DELPHI weekly report.")
    parser.add_argument("--gameweek", type=int, required=True, help="Gameweek to report on.")
    parser.add_argument(
        "--format", choices=["markdown", "text"], default="markdown", help="Output format to print."
    )
    parser.add_argument(
        "--send", action="store_true", help="Also deliver the report via Telegram (if configured) or console."
    )
    args = parser.parse_args()

    configure_logging()
    init_db()
    service = WeeklyReportService()

    with session_scope() as db:
        report = service.build_report(db, gameweek=args.gameweek)

    output = report.to_markdown() if args.format == "markdown" else report.to_plain_text()
    print(output)

    if args.send:
        if settings.telegram_bot_token and settings.telegram_chat_id:
            channel = TelegramDeliveryChannel()
        else:
            channel = ConsoleDeliveryChannel()
        result = channel.send(report)
        logger.info("Delivered via {}: {}", result.channel, result.detail)


if __name__ == "__main__":
    main()