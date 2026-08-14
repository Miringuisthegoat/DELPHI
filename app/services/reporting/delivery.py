"""
Phase 9: pluggable delivery channels for `WeeklyReport`.

Only `ConsoleDeliveryChannel` actually sends anywhere today (to the log,
which is genuinely useful for the scheduled job and for testing this
phase without any external account setup). `TelegramDeliveryChannel` and
`DiscordDeliveryChannel` are documented stubs: the project brief's
"Future Features" list calls out both, and defining the interface now
means a future phase only has to fill in `_send()`, not redesign how
reports reach a channel.

Every channel takes a `WeeklyReport` and returns a `DeliveryResult` -
callers (the API route, the scheduled job) never need to know which
channel they're talking to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
from loguru import logger

from app.core.config import settings
from app.services.reporting.service import WeeklyReport

@dataclass
class DeliveryResult:
    """Outcome of attempting to deliver one report to one channel."""

    channel: str
    delivered: bool
    detail: str


class DeliveryChannel(ABC):
    """Common interface every report-delivery destination implements."""

    #: Short identifier used in `DeliveryResult.channel` / logs.
    name: str = "base"

    @abstractmethod
    def send(self, report: WeeklyReport) -> DeliveryResult:
        """Deliver `report` to this channel, returning the outcome."""


class ConsoleDeliveryChannel(DeliveryChannel):
    """Logs the report via loguru. The only channel that works out of the box.

    Useful both as the scheduler's default (so a fresh install has a
    working weekly report immediately, no bot tokens required) and as a
    reference implementation for what a real channel's `send()` should
    return.
    """

    name = "console"

    def send(self, report: WeeklyReport) -> DeliveryResult:
        logger.info(
            "Weekly report for gw {}:\n{}", report.gameweek, report.to_plain_text()
        )
        return DeliveryResult(
            channel=self.name, delivered=True, detail="Logged to console/log file."
        )

_UNSET = object()


class TelegramDeliveryChannel(DeliveryChannel):
    """Sends the report to a Telegram chat via the Bot API.

    Requires `settings.telegram_bot_token` and `settings.telegram_chat_id`
    to be set (see `app/core/config.py`) unless explicit overrides are
    passed to the constructor. Uses `report.to_plain_text()` rather than
    markdown, since Telegram's default rendering doesn't handle our
    markdown headings well without carefully escaped MarkdownV2.
    """

    name = "telegram"

    def __init__(self, bot_token: object = _UNSET, chat_id: object = _UNSET) -> None:
        self._bot_token = settings.telegram_bot_token if bot_token is _UNSET else bot_token
        self._chat_id = settings.telegram_chat_id if chat_id is _UNSET else chat_id

    def send(self, report: WeeklyReport) -> DeliveryResult:
        if not self._bot_token or not self._chat_id:
            raise NotImplementedError(
                "Telegram delivery requires TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID to be set in .env - see this class's "
                "docstring."
            )

        text = report.to_plain_text()
        if len(text) > 4000:
            text = text[:3990] + "\n...[truncated]"

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        try:
            response = httpx.post(
                url, json={"chat_id": self._chat_id, "text": text}, timeout=10.0
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Telegram delivery failed: {}", exc)
            return DeliveryResult(
                channel=self.name, delivered=False, detail=f"Telegram API error: {exc}"
            )

        return DeliveryResult(
            channel=self.name, delivered=True, detail="Delivered to Telegram chat."
        )

class DiscordDeliveryChannel(DeliveryChannel):
    """Sends the report to a Discord channel via an incoming webhook.

    NOT YET IMPLEMENTED. Needs one setting (`discord_webhook_url: str | None`)
    and a POST of ``{"content": report.to_plain_text()}`` (or an embed
    built from `report.sections` for richer formatting) to that webhook
    URL via `httpx`. Raises `NotImplementedError` for the same reason as
    `TelegramDeliveryChannel`.
    """

    name = "discord"

    def __init__(self, webhook_url: str | None = None) -> None:
        self._webhook_url = webhook_url

    def send(self, report: WeeklyReport) -> DeliveryResult:
        raise NotImplementedError(
            "Discord delivery isn't implemented yet - see this class's docstring "
            "for the webhook payload needed to wire it up."
        )
