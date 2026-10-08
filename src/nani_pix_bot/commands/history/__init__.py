"""/history — finished games in DM (group members only): a paged table with
a button per game, an All / Mine toggle, and each game's full record with
its guess log. One rich message, edited in place; callback data in data.py,
markdown in render.py, the queries in services/game/history.py."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import outsider_refusal
from nani_pix_bot.commands.helpers.paging import clamp_page, nav_row, page_count
from nani_pix_bot.commands.helpers.rich import RichTarget, edit_rich, md_escape, send_rich
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.commands.history import render
from nani_pix_bot.commands.history.data import (
    Filter,
    GameRequest,
    ListRequest,
    game_data,
    list_data,
    parse,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.game import history

PAGE_SIZE = 10
GUESS_PAGE_SIZE = 25
BUTTONS_PER_ROW = 5
Rendered = tuple[str, InlineKeyboardMarkup]


def _list_keyboard(
    games: list, request: ListRequest, pages: int, lang: str
) -> InlineKeyboardMarkup:
    picks = [
        InlineKeyboardButton(f"#{game.id}", callback_data=game_data(GameRequest(game.id, request)))
        for game in games
    ]
    rows = [picks[i : i + BUTTONS_PER_ROW] for i in range(0, len(picks), BUTTONS_PER_ROW)]
    nav = nav_row(lambda p: list_data(ListRequest(request.filt, p)), request.page, pages, lang)
    if nav:
        rows.append(nav)
    other = Filter.ALL if request.filt is Filter.MINE else Filter.MINE
    toggle = i18n.t(f"history.filter.{other.name.lower()}", lang)
    rows.append([InlineKeyboardButton(toggle, callback_data=list_data(ListRequest(other, 0)))])
    return InlineKeyboardMarkup(rows)


def list_view(session: Session, viewer_id: int, request: ListRequest, lang: str) -> Rendered:
    player_id = viewer_id if request.filt is Filter.MINE else None
    total = history.count_finished(session, player_id=player_id)
    page = clamp_page(request.page, total=total, size=PAGE_SIZE)
    request = ListRequest(request.filt, page)
    heading = i18n.t(f"history.title.{request.filt.name.lower()}", lang)
    if total == 0:
        empty = md_escape(i18n.t(f"history.empty.{request.filt.name.lower()}", lang))
        return f"## {md_escape(heading)}\n\n{empty}", _list_keyboard([], request, 1, lang)
    games = history.finished_games(
        session, player_id=player_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE
    )
    tz = settings.get_group_timezone(session)
    markdown = render.list_markdown(session, games, heading, tz, lang)
    return markdown, _list_keyboard(games, request, page_count(total, PAGE_SIZE), lang)


def game_view(session: Session, request: GameRequest, lang: str) -> Rendered | None:
    """None when the game is gone or not over."""
    detail = history.game_detail(session, request.game_id)
    if detail is None:
        return None
    total = len(detail.guesses)
    page = clamp_page(request.guess_page, total=total, size=GUESS_PAGE_SIZE)
    shown = detail.guesses[page * GUESS_PAGE_SIZE : (page + 1) * GUESS_PAGE_SIZE]
    tz = settings.get_group_timezone(session)
    markdown = render.detail_markdown(session, detail, shown, tz, lang)
    rows = []
    nav = nav_row(
        lambda p: game_data(GameRequest(request.game_id, request.back, p)),
        page,
        page_count(total, GUESS_PAGE_SIZE),
        lang,
    )
    if nav:
        rows.append(nav)
    back = InlineKeyboardButton(i18n.t("history.back", lang), callback_data=list_data(request.back))
    rows.append([back])
    return markdown, InlineKeyboardMarkup(rows)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None or not is_private_chat(update):
        return
    refusal = await outsider_refusal(context, user.id, "history")
    if refusal is not None:
        await message.reply_text(refusal)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown, markup = list_view(session, user.id, ListRequest(), lang)
    logger.info("opened the game history")
    await send_rich(context.bot, RichTarget(message.chat_id), markdown, markup)


def _tap_view(session: Session, viewer_id: int, request: ListRequest | GameRequest, lang: str):
    if isinstance(request, ListRequest):
        logger.info(
            "paged the game history ({filter}, page {page})",
            filter=request.filt.name.lower(),
            page=request.page + 1,
        )
        return list_view(session, viewer_id, request, lang)
    rendered = game_view(session, request, lang)
    if rendered is None:
        logger.warning(
            "history tap for game {game_id}, which is gone or not over", game_id=request.game_id
        )
    else:
        logger.info(
            "viewed a game in /history (guess page {page})",
            game_id=request.game_id,
            page=request.guess_page + 1,
        )
    return rendered


async def history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    request = parse(query.data or "")
    if request is None or query.message is None or query.from_user is None:
        logger.warning("ignored a malformed or stale history tap {data!r}", data=query.data)
        await query.answer()
        return
    refusal = await outsider_refusal(context, query.from_user.id, "history tap")
    if refusal is not None:
        await query.answer(refusal, show_alert=True)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        rendered = _tap_view(session, query.from_user.id, request, lang)
    if rendered is None:
        await query.answer(i18n.t("history.stale", lang), show_alert=True)
        return
    await query.answer()
    target = RichTarget(query.message.chat.id, message_id=query.message.message_id)
    await edit_rich(context.bot, target, *rendered)
