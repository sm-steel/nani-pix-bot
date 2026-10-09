"""Telegram rich messages (Bot API 10.3, Aug 2026): headings, checkbox
lists and tables from a markdown string — sendRichMessage, and
editMessageText's `rich_message`.
python-telegram-bot 22.8 speaks Bot API 10.0, so this goes through
bot.do_api_request; switch to PTB's own methods once it supports them. A
rejected rich message (an old server, a markdown edge case) falls back to
the same text sent plain, with an ERROR, so a view never silently vanishes.
A message over RICH_LIMIT is cut at a line break first (fit), also with an
ERROR: otherwise both the rich and the plain send are rejected and a tap
just does nothing."""

import re
from dataclasses import dataclass
from typing import Any

from loguru import logger
from telegram import Bot, InlineKeyboardMarkup, Message
from telegram.error import BadRequest, InvalidToken

from nani_pix_bot.services.text import cut_at_line

# Assumed equal to Telegram's text message limit. An approximation: fit counts
# Python code points of the markdown source, Telegram counts UTF-16 units of the
# parsed text. The markup and escapes that drop out usually outweigh emoji
# counting double, so it errs on the safe side.
RICH_LIMIT = 4096
CUT_MARKER = "\n\n…"

# ASCII punctuation markdown can give meaning to; a backslash makes each literal.
_SPECIAL = re.compile(r"([\\`*_~|=\[\](){}#>!+\-.])")


def md_escape(text: str) -> str:
    """For anything that isn't ours to format: player names, titles, input."""
    return _SPECIAL.sub(r"\\\1", text)


_ESCAPED = re.compile(r"\\" + _SPECIAL.pattern)


def _unescape(markdown: str) -> str:
    """Undo md_escape for the plain-text fallback (markdown syntax stays)."""
    return _ESCAPED.sub(r"\1", markdown)


def fit(markdown: str) -> str:
    """`markdown` cut at a line break to fit RICH_LIMIT, if it doesn't."""
    if len(markdown) <= RICH_LIMIT:
        return markdown
    logger.error("rich message of {length} chars cut to fit", length=len(markdown))
    return cut_at_line(markdown, RICH_LIMIT, CUT_MARKER)


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
    markdown = fit(markdown)
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
    markdown = fit(markdown)
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
