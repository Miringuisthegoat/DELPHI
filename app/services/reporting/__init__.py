"""Weekly reporting package: turns Phase 8's DashboardView into a prose
gameweek report, plus pluggable delivery channels (console/Telegram/Discord)."""

from app.services.reporting.delivery import (
    ConsoleDeliveryChannel,
    DeliveryChannel,
    DeliveryResult,
    DiscordDeliveryChannel,
    TelegramDeliveryChannel,
)
from app.services.reporting.service import WeeklyReport, WeeklyReportService

__all__ = [
    "ConsoleDeliveryChannel",
    "DeliveryChannel",
    "DeliveryResult",
    "DiscordDeliveryChannel",
    "TelegramDeliveryChannel",
    "WeeklyReport",
    "WeeklyReportService",
]
