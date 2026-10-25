"""The /profile card's buttons (#412), all in the card's own message: ◀ ▶ swap
the photo for the neighbouring player's card (services/cosmetics/profile.py's
roster order, wrapping around), 👥 Players turns the caption and buttons into
a page of names, and a name swaps in that player's card. On your own card in
DM, ✏️ opens edit.py's editor. Callback data: data.py."""

from dataclasses import dataclass
from typing import cast

from loguru import logger
from sqlalchemy.orm import Session
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import paging, someone_else
from nani_pix_bot.commands.profile import card, edit
from nani_pix_bot.commands.profile.data import (
    SOMEONE_ELSE_PREFIX,
    VIAS,
    card_data,
    edit_data,
    list_data,
    parse,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import ShowcaseKind
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cosmetics import profile

PAGE_SIZE = 8


def _page_of(order: list[int], player_id: int) -> int:
    return order.index(player_id) // PAGE_SIZE if player_id in order else 0


@dataclass(frozen=True)
class Viewing:
    """Whose card is on screen: `owner` sent /profile and is the one who may tap."""

    owner: int
    target: int
    private: bool  # in a DM with the bot

    @property
    def editable(self) -> bool:
        """Only your own card, and only in DM, offers the editor."""
        return self.private and self.owner == self.target


def card_keyboard(order: list[int], viewing: Viewing, lang: str) -> InlineKeyboardMarkup:
    """◀ ▶, 👥 Players, 👥 Someone else on your own card and, on an editable
    card, ✏️."""
    owner, target = viewing.owner, viewing.target
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
    if owner == target:
        rows.append([someone_else.button(SOMEONE_ELSE_PREFIX, "profile", owner, lang)])
    if viewing.editable:
        edit_button = InlineKeyboardButton(
            i18n.t("profile.edit", lang), callback_data=edit_data(owner, ShowcaseKind.STAMP, 0)
        )
        rows.append([edit_button])
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


def roster(session: Session, context: ContextTypes.DEFAULT_TYPE, viewer_id: int) -> list[int]:
    """Who the list and ◀ ▶ walk for `viewer_id`: players who play, never the bot."""
    return profile.roster(session, viewer_id=viewer_id, bot_id=context.bot.id)


async def _show_card(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    fields: list[int | str],
    lang: str,
) -> None:
    owner, target, via = cast(tuple[int, int, str], tuple(fields))
    with session_scope(context.bot_data["session_factory"]) as session:
        order = roster(session, context, owner)
        if target not in order:
            logger.warning(
                "a /profile button pointed at unknown player {target_id}", target_id=target
            )
            await query.answer(i18n.t("profile.gone", lang), show_alert=True)
            return
        view, headline = card.load(session, target, lang)
        private = query.message is not None and query.message.chat.type == ChatType.PRIVATE
        keyboard = card_keyboard(order, Viewing(owner, target, private), lang)
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
        caption, keyboard, page = list_view(
            session, roster(session, context, owner), owner, page, lang
        )
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
    handlers = {"o": _show_card, "l": _show_list, "e": edit.editor_tap, "t": edit.toggle_tap}
    await handlers[action](query, context, fields, lang)
