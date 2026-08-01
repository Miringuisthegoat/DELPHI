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

from loguru import logger

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


class TelegramDeliveryChannel(DeliveryChannel):
    """Sends the report to a Telegram chat via the Bot API.

    NOT YET IMPLEMENTED - this is the documented seam for a future phase.
    Wiring this up needs two settings (add to `app/core/config.py`):

        telegram_bot_token: str | None = None
        telegram_chat_id: str | None = None

    and a POST to
    ``https://api.telegram.org/bot{token}/sendMessage`` with
    ``{"chat_id": chat_id, "text": report.to_plain_text()}`` via `httpx`
    (already a project dependency - see `app.services.fpl_api.client`).
    Raises `NotImplementedError` rather than silently no-op'ing, so a
    misconfigured "send to Telegram" call fails loudly instead of
    pretending to have delivered something.
    """

    name = "telegram"

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id

    def send(self, report: WeeklyReport) -> DeliveryResult:
        raise NotImplementedError(
            "Telegram delivery isn't implemented yet - see this class's docstring "
            "for the two settings and the httpx call needed to wire it up."
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
