"""The /profile card's buttons (#412), all in the card's own message: ◀ ▶ swap
the photo for the neighbouring player's card (services/cosmetics/profile.py's
roster order, wrapping around), 👥 Players turns the caption and buttons into
a page of names, and a name swaps in that player's card.

Callback data: prof:o:<owner>:<target>:<via> (show a card) and
prof:l:<owner>:<page> (the player list). <owner> is whoever sent /profile; in
the topic only they may tap. Everything after the prefix is client-controlled,
so it is parsed defensively."""

from typing import cast

from loguru import logger
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID
from nani_pix_bot.commands.helpers import paging
from nani_pix_bot.commands.profile import card
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cosmetics import profile

PREFIX = "prof:"
PAGE_SIZE = 8
# How a card was reached, as it reads in the log.
VIAS = {"p": "◀", "n": "▶", "l": "the player list", "m": "my card"}
# action -> its fields, and which of them are ints
_SHAPES: dict[str, tuple[bool, ...]] = {"o": (True, True, False), "l": (True, True)}


def card_data(owner: int, target: int, via: str) -> str:
    return f"{PREFIX}o:{owner}:{target}:{via}"


def list_data(owner: int, page: int) -> str:
    return f"{PREFIX}l:{owner}:{page}"


def parse(data: str) -> tuple[str, list[int | str]] | None:
    action, *fields = data.removeprefix(PREFIX).split(":")
    shape = _SHAPES.get(action)
    if shape is None or len(fields) != len(shape):
        return None
    pairs = list(zip(fields, shape, strict=True))
    if any(is_int and not (raw.isdecimal() and int(raw) <= MAX_ID) for raw, is_int in pairs):
        return None
    if action == "o" and fields[2] not in VIAS:
        return None
    return action, [int(raw) if is_int else raw for raw, is_int in pairs]


def _page_of(order: list[int], player_id: int) -> int:
    return order.index(player_id) // PAGE_SIZE if player_id in order else 0


def card_keyboard(order: list[int], owner: int, target: int, lang: str) -> InlineKeyboardMarkup:
    rows = []
    if len(order) > 1:
        prev_id = profile.neighbour(order, target, -1)
        next_id = profile.neighbour(order, target, 1)
        rows.append(
            [
                InlineKeyboardButton(
                    i18n.t("profile.prev", lang), callback_data=card_data(owner, prev_id, "p")
                ),
                InlineKeyboardButton(
                    i18n.t("profile.next", lang), callback_data=card_data(owner, next_id, "n")
                ),
            ]
        )
    players_button = InlineKeyboardButton(
        i18n.t("profile.players", lang), callback_data=list_data(owner, _page_of(order, target))
    )
    rows.append([players_button])
    return InlineKeyboardMarkup(rows)


def list_view(
    session, order: list[int], owner: int, page: int, lang: str
) -> tuple[str, InlineKeyboardMarkup, int]:
    """The list's caption, its keyboard (a page of names, ◀ n/N ▶, my card) and
    the page actually shown."""
    page = paging.clamp_page(page, total=len(order), size=PAGE_SIZE)
    shown = order[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows = [
        [
            InlineKeyboardButton(
                players.display_name(session, player_id),
                callback_data=card_data(owner, player_id, "l"),
            )
        ]
        for player_id in shown
    ]
    pages = paging.page_count(len(order), PAGE_SIZE)
    nav = paging.nav_row(lambda p: list_data(owner, p), page, pages, lang)
    rows += [nav] if nav else []
    mine = InlineKeyboardButton(
        i18n.t("profile.my_card", lang), callback_data=card_data(owner, owner, "m")
    )
    rows.append([mine])
    return i18n.t("profile.list", lang), InlineKeyboardMarkup(rows), page


async def _show_card(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    fields: list[int | str],
    lang: str,
) -> None:
    owner, target, via = cast(tuple[int, int, str], tuple(fields))
    with session_scope(context.bot_data["session_factory"]) as session:
        order = profile.roster(session)
        if target not in order:
            logger.warning(
                "a /profile button pointed at unknown player {target_id}", target_id=target
            )
            await query.answer(i18n.t("profile.gone", lang), show_alert=True)
            return
        view, headline = card.load(session, target, lang)
        keyboard = card_keyboard(order, owner, target, lang)
    png, caption = await card.photo(context, view, headline, lang)
    try:
        await query.edit_message_media(
            InputMediaPhoto(media=png, caption=caption), reply_markup=keyboard
        )
    except TelegramError:
        logger.opt(exception=True).warning(
            "could not switch the /profile card to {target}", target=view.name, target_id=target
        )
        await query.answer()
        return
    await query.answer()
    logger.info(
        "switched the /profile card to {target} via {via}",
        target=view.name,
        target_id=target,
        via=VIAS[via],
    )


async def _show_list(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    fields: list[int | str],
    lang: str,
) -> None:
    owner, page = cast(tuple[int, int], tuple(fields))
    with session_scope(context.bot_data["session_factory"]) as session:
        caption, keyboard, page = list_view(session, profile.roster(session), owner, page, lang)
    try:
        await query.edit_message_caption(caption=caption, reply_markup=keyboard)
    except TelegramError:
        logger.opt(exception=True).warning("could not show the /profile player list")
        await query.answer()
        return
    await query.answer()
    logger.info("opened the /profile player list (page {page})", page=page + 1)


async def profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    parsed = parse(query.data)
    if parsed is None:
        logger.warning("ignored a malformed /profile button {data!r}", data=query.data)
        await query.answer()
        return
    action, fields = parsed
    owner = cast(int, fields[0])
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        owner_name = players.describe_player_id(session, owner)
    if query.from_user.id != owner:
        logger.warning("refused a tap on {owner}'s /profile card", owner=owner_name, owner_id=owner)
        await query.answer(i18n.t("profile.not_yours", lang), show_alert=True)
        return
    show = _show_list if action == "l" else _show_card
    await show(query, context, fields, lang)
