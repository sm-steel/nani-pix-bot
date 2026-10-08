"""The achievements browser in DM (spec §5): one rich message, edited in
place — All / Earned / Not yet tabs, ◀ page ▶, Compare and Top. Callback
data: ach:v:<owner>:<view>:<page>, ach:c:<other>:<filter>:<page> (compare.py),
ach:t:<page>. The page counter is helpers/paging.py's NOOP (ach:x on
older messages, still answered here). Everything after the prefix
is client-controlled, so it is parsed defensively (see dm_start/keyboards.py's
_validated_index)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements import render
from nani_pix_bot.commands.achievements.common import MAX_ID, PREFIX, outsider_refusal
from nani_pix_bot.commands.helpers.paging import clamp_page, nav_row, page_count
from nani_pix_bot.commands.helpers.rich import RichTarget, edit_rich, md_escape, send_rich
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.achievements import status
from nani_pix_bot.services.achievements.status import View

PAGE_SIZE = 8
TOP_PAGE_SIZE = 10
NOOP = f"{PREFIX}x"  # the page counter on messages sent before paging.NOOP
Rendered = tuple[str, InlineKeyboardMarkup]
# action -> how many fields it carries, and which of them are ints
_SHAPES: dict[str, tuple[bool, ...]] = {
    "v": (True, False, True),
    "c": (True, False, True),
    "t": (True,),
}


@dataclass(frozen=True)
class Browse:
    viewer_id: int
    owner_id: int
    view: View = View.ALL
    page: int = 0


def view_data(owner_id: int, view: View, page: int) -> str:
    return f"{PREFIX}v:{owner_id}:{view.value}:{page}"


def top_data(page: int) -> str:
    return f"{PREFIX}t:{page}"


def parse(data: str) -> tuple[str, list[int | str]] | None:
    action, *fields = data.removeprefix(PREFIX).split(":")
    shape = _SHAPES.get(action)
    if shape is None or len(fields) != len(shape):
        return None
    pairs = list(zip(fields, shape, strict=True))
    if any(is_int and not (raw.isdecimal() and int(raw) <= MAX_ID) for raw, is_int in pairs):
        return None
    return action, [int(raw) if is_int else raw for raw, is_int in pairs]


def _tab(view: View, current: View, owner_id: int, lang: str) -> InlineKeyboardButton:
    label = i18n.t(f"achievements.tab.{view.name.lower()}", lang)
    marker = "● " if view is current else ""
    return InlineKeyboardButton(marker + label, callback_data=view_data(owner_id, view, 0))


def _keyboard(request: Browse, pages: int, lang: str) -> InlineKeyboardMarkup:
    rows = [[_tab(v, request.view, request.owner_id, lang) for v in View]]
    nav = nav_row(lambda p: view_data(request.owner_id, request.view, p), request.page, pages, lang)
    if nav:
        rows.append(nav)
    extra = []
    if request.viewer_id != request.owner_id:
        compare = f"{PREFIX}c:{request.owner_id}:a:0"
        extra.append(
            InlineKeyboardButton(i18n.t("achievements.compare", lang), callback_data=compare)
        )
    extra.append(
        InlineKeyboardButton(i18n.t("achievements.top_button", lang), callback_data=top_data(0))
    )
    rows.append(extra)
    return InlineKeyboardMarkup(rows)


def browse_view(session: Session, request: Browse, lang: str) -> Rendered:
    items = status.build(session, request.owner_id, datetime.now(UTC))
    ordered = status.ordered(items, request.view)
    page = clamp_page(request.page, total=len(ordered), size=PAGE_SIZE)
    pages = page_count(len(ordered), PAGE_SIZE)
    header = "# 🏅 " + md_escape(players.display_name(session, request.owner_id))
    header += "\n" + render.header_line(session, request.owner_id, items, lang)
    shown = ordered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    fixed = Browse(request.viewer_id, request.owner_id, request.view, page)
    return render.page(session, header, shown, lang), _keyboard(fixed, pages, lang)


def top_view(session: Session, page: int, lang: str) -> Rendered:
    total = status.ranked_count(session)
    page = clamp_page(page, total=total, size=TOP_PAGE_SIZE)
    pages = page_count(total, TOP_PAGE_SIZE)
    rows = status.top(session, limit=TOP_PAGE_SIZE, offset=page * TOP_PAGE_SIZE)
    markdown = render.top_table(session, rows, lang, page * TOP_PAGE_SIZE)
    nav = nav_row(top_data, page, pages, lang)
    return markdown, InlineKeyboardMarkup([nav] if nav else [])


def _view_tap(session: Session, viewer_id: int, fields: list, lang: str) -> Rendered:
    owner_id, view, page = fields
    if view not in {v.value for v in View}:
        view = View.ALL.value
    logger.info(
        "browsed achievements of {target} ({view}, page {page})",
        target=players.describe_player_id(session, owner_id),
        target_id=owner_id,
        view=view,
        page=page + 1,
    )
    return browse_view(session, Browse(viewer_id, owner_id, View(view), page), lang)


def _top_tap(session: Session, _viewer_id: int, fields: list, lang: str) -> Rendered:
    logger.info("paged the achievements top to page {page}", page=fields[0] + 1)
    return top_view(session, fields[0], lang)


ACTIONS: dict[str, Callable[[Session, int, list, str], Rendered]] = {"v": _view_tap, "t": _top_tap}


async def open_browser(
    message: Message, context: ContextTypes.DEFAULT_TYPE, request: Browse
) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown, markup = browse_view(session, request, lang)
        target = players.describe_player_id(session, request.owner_id)
    logger.info(
        "opened the achievements browser for {target}", target=target, target_id=request.owner_id
    )
    await send_rich(context.bot, RichTarget(message.chat_id), markdown, markup)


async def open_top(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/achievements top` in DM: the same Top view the browser's button opens."""
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown, markup = top_view(session, 0, lang)
    logger.info("opened the achievements top in DM")
    await send_rich(context.bot, RichTarget(message.chat_id), markdown, markup)


def _valid_tap(query: CallbackQuery) -> tuple[str, list] | None:
    parsed = parse(query.data or "")
    if (
        parsed is None
        or parsed[0] not in ACTIONS
        or query.message is None
        or query.from_user is None
    ):
        logger.warning("ignored a malformed or stale achievements tap {data!r}", data=query.data)
        return None
    return parsed


async def achievements_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    parsed = None if query.data == NOOP else _valid_tap(query)
    if parsed is None or query.message is None:
        await query.answer()
        return
    refusal = await outsider_refusal(context, query.from_user.id, "achievements tap")
    await query.answer(refusal, show_alert=refusal is not None)
    if refusal is not None:
        return
    action, fields = parsed
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown, markup = ACTIONS[action](session, query.from_user.id, fields, lang)
    target = RichTarget(query.message.chat.id, message_id=query.message.message_id)
    await edit_rich(context.bot, target, markdown, markup)
