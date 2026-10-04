"""Building and sending the text clues a buyer paid for: the clue message
itself (also reused by the share flow), the DM, and the topic notice."""

import html

from loguru import logger
from telegram import InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.clues import text

_UNKNOWN_CHAR = "?"


def text_clue_message(game: Game, kind: ClueKind, owned: set[ClueKind], lang: str) -> str:
    """The HTML message for a text clue. `owned` is every text clue the
    buyer holds: a shape message fills in the letters they also bought."""
    found = game_service.display_title_field(game, lang)
    if found is None:
        logger.error("Game {} has no title for a {} clue", game.id, kind)
        return i18n.t("shop.stale", lang)
    field, title = found
    field_name = i18n.t(f"title_field.{field.value}", lang)
    if kind is ClueKind.FIRST_LETTER:
        letter = text.first_char(title) or _UNKNOWN_CHAR
        return i18n.t("clue.first_letter", lang, field=field_name, letter=html.escape(letter))
    if kind is ClueKind.LAST_LETTER:
        letter = text.last_char(title) or _UNKNOWN_CHAR
        return i18n.t("clue.last_letter", lang, field=field_name, letter=html.escape(letter))
    shape = text.title_shape(
        title,
        reveal_first=ClueKind.FIRST_LETTER in owned,
        reveal_last=ClueKind.LAST_LETTER in owned,
    )
    lengths = ", ".join(map(str, text.word_lengths(title)))
    return i18n.t(
        "clue.title_shape", lang, field=field_name, shape=html.escape(shape), lengths=lengths
    )


async def deliver_text_clue(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    message: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """DM the clue; False (logged) if Telegram refused it, so the caller
    can refund."""
    try:
        await context.bot.send_message(
            chat_id=user_id, text=message, parse_mode="HTML", reply_markup=reply_markup
        )
    except TelegramError:
        logger.exception("Could not DM a clue to {}", user_id)
        return False
    return True


async def deliver_image_clue(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    photo: bytes,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> str | None:
    """DM the clue picture; its Telegram file_id (for the share flow), or
    None (logged) if Telegram refused it, so the caller can refund."""
    try:
        message = await context.bot.send_photo(
            chat_id=user_id, photo=photo, caption=caption, reply_markup=reply_markup
        )
    except TelegramError:
        logger.exception("Could not DM an image clue to {}", user_id)
        return None
    return message.photo[-1].file_id


async def post_bought_notice(
    context: ContextTypes.DEFAULT_TYPE, buyer_name: str, kind: ClueKind, lang: str
) -> None:
    """Tell the game topic that someone bought a clue (not what it says)."""
    notice = i18n.t(
        "shop.bought_notice",
        lang,
        name=buyer_name,
        item=i18n.t(f"shop.item_name.{kind.value}", lang),
    )
    try:
        await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=notice,
        )
    except TelegramError:
        logger.warning("Could not post the clue-bought notice for {}", kind, exc_info=True)


async def share_clue(context: ContextTypes.DEFAULT_TYPE, text: str, file_id: str | None) -> bool:
    """Post a bought clue to the game topic: the photo (by its stored
    file_id) with `text` as caption, or `text` alone as an HTML message.
    False (logged) if Telegram refused it, so the caller can undo the mark."""
    target = {
        "chat_id": context.bot_data["group_chat_id"],
        "message_thread_id": context.bot_data["game_topic_id"],
    }
    try:
        if file_id is None:
            await context.bot.send_message(text=text, parse_mode="HTML", **target)
        else:
            await context.bot.send_photo(photo=file_id, caption=text, parse_mode="HTML", **target)
    except TelegramError:
        logger.exception("Could not share a clue to the game topic")
        return False
    return True
