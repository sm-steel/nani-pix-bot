"""Building and sending the text clues a buyer paid for: the clue message
itself (also reused by the share flow), the DM, and the topic notice."""

import html
from collections.abc import Callable

from loguru import logger
from telegram import InlineKeyboardMarkup, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.clues import text
from nani_pix_bot.services.game import TitleField


def _letter_lines(
    titles: list[tuple[TitleField, str]],
    pick: Callable[..., str | None],
    lang: str,
    *,
    numbers_matter: bool,
) -> list[str]:
    """One bullet per title that has a letter at all."""
    lines = []
    for field, title in titles:
        letter = pick(title, numbers_matter=numbers_matter)
        if letter is None:
            continue
        field_name = i18n.t(f"title_field.{field.value}", lang)
        lines.append(i18n.t("clue.letter_line", lang, field=field_name, letter=html.escape(letter)))
    return lines


def _shape_entries(
    titles: list[tuple[TitleField, str]], owned: set[ClueKind], lang: str, *, numbers_matter: bool
) -> list[str]:
    """One shape block per title, with the letters the buyer owns filled in."""
    return [
        i18n.t(
            "clue.title_shape_entry",
            lang,
            field=i18n.t(f"title_field.{field.value}", lang),
            shape=html.escape(
                text.title_shape(
                    title,
                    reveal_first=ClueKind.FIRST_LETTER in owned,
                    reveal_last=ClueKind.LAST_LETTER in owned,
                    numbers_matter=numbers_matter,
                )
            ),
            lengths=", ".join(map(str, text.word_lengths(title, numbers_matter=numbers_matter))),
        )
        for field, title in titles
    ]


def text_clue_message(game: Game, kind: ClueKind, owned: set[ClueKind], lang: str) -> str:
    """The HTML message for a text clue, covering every title in
    game_service.clue_titles(). `owned` is every text clue the buyer
    holds: a shape message fills in the letters they also bought."""
    titles = game_service.clue_titles(game, lang)
    if not titles:
        logger.error("no title for a {kind} clue", kind=kind.value, game_id=game.id)
        return i18n.t("shop.stale", lang)
    if kind is ClueKind.TITLE_SHAPE:
        body = _shape_entries(titles, owned, lang, numbers_matter=bool(game.numbers_matter))
    else:
        pick = text.first_char if kind is ClueKind.FIRST_LETTER else text.last_char
        body = _letter_lines(titles, pick, lang, numbers_matter=bool(game.numbers_matter))
    return "\n".join([i18n.t(f"clue.{kind.value}", lang), *body])


async def deliver_text_clue(
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    message: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """DM the clue; False (logged) if Telegram refused it, so the caller
    can refund."""
    try:
        await context.bot.send_message(
            chat_id=user.id, text=message, parse_mode="HTML", reply_markup=reply_markup
        )
    except TelegramError:
        logger.exception("could not DM a text clue")
        return False
    return True


async def deliver_image_clue(
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    photo: bytes,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
) -> str | None:
    """DM the clue picture; its Telegram file_id (for the share flow), or
    None (logged) if Telegram refused it, so the caller can refund."""
    try:
        message = await context.bot.send_photo(
            chat_id=user.id, photo=photo, caption=caption, reply_markup=reply_markup
        )
    except TelegramError:
        logger.exception("could not DM an image clue")
        return None
    return message.photo[-1].file_id


async def post_bought_notice(
    context: ContextTypes.DEFAULT_TYPE, game_id: int, buyer_name: str, kind: ClueKind, lang: str
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
        logger.opt(exception=True).warning(
            "could not post the {kind} clue-bought notice", kind=kind.value, game_id=game_id
        )
        return
    logger.info(
        "posted the {kind} clue-bought notice to the group", kind=kind.value, game_id=game_id
    )


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
        logger.exception("could not share a clue to the game topic")
        return False
    return True
