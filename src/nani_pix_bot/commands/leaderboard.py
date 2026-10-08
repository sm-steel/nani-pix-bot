"""The /leaderboard command — see MECHANICS.md's "Leaderboard" section.

All-time board as one rich-message table (👑 wins, 💠 balance, 🏆
achievement points), paged in place with lb:<page>. Works in the game topic
and, for group members, in DM. Only the DM copy has a "you" line: the topic
message is shared, so whoever pages it isn't who it would describe."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID, outsider_refusal
from nani_pix_bot.commands.helpers.paging import clamp_page, nav_row, page_count
from nani_pix_bot.commands.helpers.rich import RichTarget, edit_rich, md_escape, send_rich
from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.achievements import titles
from nani_pix_bot.services.players import LeaderRow

LEADERBOARD_SIZE = 10
PREFIX = "lb:"
Rendered = tuple[str, InlineKeyboardMarkup]


def page_data(page: int) -> str:
    return f"{PREFIX}{page}"


def parse_page(data: str) -> int | None:
    raw = data.removeprefix(PREFIX) if data.startswith(PREFIX) else ""
    return int(raw) if raw.isdecimal() and int(raw) <= MAX_ID else None


def _name(session: Session, row: LeaderRow, lang: str) -> str:
    name = players.display_name(session, row.player_id)
    title = titles.text(row.title_key, lang)
    return f"{name} «{title}»" if title else name


def _you_line(session: Session, viewer_id: int, shown: list[LeaderRow], lang: str) -> str:
    if any(row.player_id == viewer_id for row in shown):
        return ""
    rank = players.leaderboard_rank(session, viewer_id)
    if rank is None:
        return md_escape(i18n.t("leaderboard.you_unranked", lang))
    row = players.leaderboard(session, limit=1, offset=rank - 1)[0]
    you = i18n.t(
        "leaderboard.you",
        lang,
        rank=rank,
        wins=row.wins,
        currency=row.currency,
        points=row.points,
    )
    return md_escape(you)


def view(session: Session, page: int, lang: str, viewer_id: int | None = None) -> Rendered:
    """The page as markdown plus its ◀ n/N ▶ row; `viewer_id` adds their own
    line when they aren't on the page (DM only)."""
    header = "## " + md_escape(i18n.t("leaderboard.header", lang))
    total = players.leaderboard_count(session)
    if total == 0:
        empty = md_escape(i18n.t("leaderboard.empty", lang))
        return f"{header}\n\n{empty}", InlineKeyboardMarkup([])
    page = clamp_page(page, total=total, size=LEADERBOARD_SIZE)
    offset = page * LEADERBOARD_SIZE
    shown = players.leaderboard(session, limit=LEADERBOARD_SIZE, offset=offset)
    player = md_escape(i18n.t("standings.player", lang))
    lines = [header, "", f"| # | {player} | 👑 | 💠 | 🏆 |", "|---|---|---|---|---|"]
    lines += [
        f"| {rank} | {md_escape(_name(session, row, lang))} | {row.wins} | {row.currency} "
        f"| {row.points} |"
        for rank, row in enumerate(shown, start=offset + 1)
    ]
    you = _you_line(session, viewer_id, shown, lang) if viewer_id is not None else ""
    if you:
        lines += ["", you]
    nav = nav_row(page_data, page, page_count(total, LEADERBOARD_SIZE), lang)
    return "\n".join(lines), InlineKeyboardMarkup([nav] if nav else [])


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return
    private = is_private_chat(update)
    if private:
        refusal = await outsider_refusal(context, user.id, "leaderboard")
        if refusal is not None:
            await message.reply_text(refusal)
            return
    elif not is_game_topic(
        update,
        group_chat_id=context.bot_data["group_chat_id"],
        game_topic_id=context.bot_data["game_topic_id"],
    ):
        return

    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown, markup = view(session, 0, lang, user.id if private else None)
    logger.info("viewed the leaderboard")
    target = RichTarget(message.chat_id, thread_id=message.message_thread_id)
    await send_rich(context.bot, target, markdown, markup)


async def leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    page = parse_page(query.data or "")
    if page is None or query.message is None or query.from_user is None:
        logger.warning("ignored a malformed or stale leaderboard tap {data!r}", data=query.data)
        await query.answer()
        return
    private = query.message.chat.type == "private"
    refusal = (
        await outsider_refusal(context, query.from_user.id, "leaderboard tap") if private else None
    )
    await query.answer(refusal, show_alert=refusal is not None)
    if refusal is not None:
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown, markup = view(session, page, lang, query.from_user.id if private else None)
    logger.info("paged the leaderboard to page {page}", page=page + 1)
    target = RichTarget(query.message.chat.id, message_id=query.message.message_id)
    await edit_rich(context.bot, target, markdown, markup)
