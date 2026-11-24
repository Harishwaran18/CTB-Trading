"""Telegram delivery with retries and HTML-safe formatting."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import NetworkError, RetryAfter, TelegramError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import Settings, load_settings
from models import Direction, Signal

log = logging.getLogger("ctb.telegram")

_MAX_MESSAGE = 4096

_DIRECTION_STYLE = {
    Direction.BUY: ("\U0001F7E2", "BUY / LONG"),
    Direction.SELL: ("\U0001F534", "SELL / SHORT"),
    Direction.NEUTRAL: ("\u26AA", "NEUTRAL"),
}

_ASSET_ICON = {"crypto": "\u20BF", "forex": "\U0001F4B1", "stock": "\U0001F4C8"}


def _bar(confidence: float, width: int = 10) -> str:
    filled = max(0, min(width, round(confidence / 100 * width)))
    return "\u2588" * filled + "\u2591" * (width - filled)


def format_signal(signal: Signal, settings: Settings | None = None) -> str:
    settings = settings or load_settings()
    icon, label = _DIRECTION_STYLE[signal.direction]
    asset_icon = _ASSET_ICON.get(signal.asset_class, "\U0001F4CA")
    binary = signal.binary_direction
    expiry = signal.interval

    lines = [
        f"{icon} <b>{html.escape(signal.symbol)}</b>  {asset_icon} "
        f"<i>{html.escape(signal.asset_class)}</i>",
        "",
        f"<b>Signal:</b> {label}"
        + (f"  ({binary}, {expiry} expiry)" if binary else ""),
        f"<b>Confidence:</b> {signal.confidence:.0f}% {_bar(signal.confidence)}",
        f"<b>Regime:</b> {html.escape(signal.regime)}",
        "",
        f"<b>Price:</b> {signal.price}",
        f"<b>Entry:</b> {signal.entry}",
        f"<b>Stop:</b> {signal.stop_loss}",
        f"<b>Target:</b> {signal.take_profit}",
        f"<b>R:R:</b> 1 : {signal.risk_reward}",
        f"<b>Timeframe:</b> {html.escape(signal.interval)}",
    ]

    if signal.reasons:
        top = signal.reasons[:5]
        lines.append("")
        lines.append("<b>Why:</b>")
        lines.extend(f"  \u2022 {html.escape(reason)}" for reason in top)

    stamp = datetime.fromtimestamp(signal.generated_at, tz=timezone.utc)
    lines.extend(
        [
            "",
            f"<i>UTC {stamp:%Y-%m-%d %H:%M:%S} | candle {signal.candle_time}</i>",
            "<i>Not financial advice. Trade your own risk.</i>",
        ]
    )

    message = "\n".join(lines)
    return message[:_MAX_MESSAGE]


@retry(
    retry=retry_if_exception_type((NetworkError, RetryAfter)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=20),
    reraise=True,
)
async def _send(bot: Bot, chat_id: str, text: str) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def send_message(text: str, settings: Settings | None = None) -> bool:
    settings = settings or load_settings()
    if not settings.enable_telegram:
        log.info("Telegram disabled; message not sent")
        return False
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.error("Telegram token or chat id missing")
        return False
    try:
        bot = Bot(settings.telegram_bot_token)
        await _send(bot, settings.telegram_chat_id, text)
        return True
    except TelegramError as exc:
        log.error("Telegram send failed: %s", exc)
        return False


async def send_signal(payload: Signal | str, settings: Settings | None = None) -> bool:
    """Send a :class:`Signal` (or a raw string for backwards compatibility)."""
    settings = settings or load_settings()
    if isinstance(payload, Signal):
        text = format_signal(payload, settings)
    else:
        text = str(payload)[:_MAX_MESSAGE]
    return await send_message(text, settings)


async def verify(settings: Settings | None = None) -> dict:
    """Connectivity check used by the ``test-telegram`` CLI command."""
    settings = settings or load_settings()
    bot = Bot(settings.telegram_bot_token)
    me = await bot.get_me()
    await _send(bot, settings.telegram_chat_id, "\u2705 CTB-Trading bot connected")
    return {"username": me.username, "name": me.first_name, "chat_id": settings.telegram_chat_id}
