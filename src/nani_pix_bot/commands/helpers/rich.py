"""Telegram rich messages (Bot API 10.3, Aug 2026): headings, checkbox
lists and tables from a markdown string — sendRichMessage, and
editMessageText's `rich_message`.
python-telegram-bot 22.8 speaks Bot API 10.0, so this goes through
bot.do_api_request; switch to PTB's own methods once it supports them. A
rejected rich message (an old server, a markdown edge case) falls back to
the same text sent plain, with an ERROR, so a view never silently vanishes."""

import re
from dataclasses import dataclass
from typing import Any

from loguru import logger
from telegram import Bot, InlineKeyboardMarkup, Message
from telegram.error import BadRequest, InvalidToken

# ASCII punctuation markdown can give meaning to; a backslash makes each literal.
_SPECIAL = re.compile(r"([\\`*_~|=\[\](){}#>!+\-.])")


def md_escape(text: str) -> str:
    """For anything that isn't ours to format: player names, titles, input."""
    return _SPECIAL.sub(r"\\\1", text)


_ESCAPED = re.compile(r"\\" + _SPECIAL.pattern)


def _unescape(markdown: str) -> str:
    """Undo md_escape for the plain-text fallback (markdown syntax stays)."""
    return _ESCAPED.sub(r"\1", markdown)


@dataclass(frozen=True)
class RichTarget:
    chat_id: int
    thread_id: int | None = None
    message_id: int | None = None


def _payload(
    target: RichTarget, markdown: str, markup: InlineKeyboardMarkup | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"chat_id": target.chat_id}
    if target.thread_id is not None:
        payload["message_thread_id"] = target.thread_id
    if target.message_id is not None:
        payload["message_id"] = target.message_id
    payload["rich_message"] = {"markdown": markdown}
    if markup is not None:
        payload["reply_markup"] = markup
    return payload


async def send_rich(
    bot: Bot, target: RichTarget, markdown: str, markup: InlineKeyboardMarkup | None = None
) -> Message | None:
    try:
        return await bot.do_api_request(
            "sendRichMessage", api_kwargs=_payload(target, markdown, markup), return_type=Message
        )
    except (BadRequest, InvalidToken) as exc:  # a server without the method answers 404
        logger.error("rich message rejected ({error}) — sending it as plain text", error=exc)
        return await bot.send_message(
            chat_id=target.chat_id,
            message_thread_id=target.thread_id,
            text=_unescape(markdown),
            reply_markup=markup,
        )


async def edit_rich(
    bot: Bot, target: RichTarget, markdown: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    if target.message_id is None:
        msg = "edit_rich needs a message_id"
        raise ValueError(msg)
    try:
        await bot.do_api_request(
            "editMessageText", api_kwargs=_payload(target, markdown, markup), return_type=Message
        )
    except (BadRequest, InvalidToken) as exc:
        if "not modified" in str(exc).lower():
            logger.debug("rich message unchanged — nothing to edit")
            return
        logger.error("rich edit rejected ({error}) — editing as plain text", error=exc)
        await bot.edit_message_text(
            chat_id=target.chat_id,
            message_id=target.message_id,
            text=_unescape(markdown),
            reply_markup=markup,
        )
