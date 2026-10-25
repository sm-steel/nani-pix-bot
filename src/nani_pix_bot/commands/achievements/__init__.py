"""/achievements (spec §5). In the game topic: a one-message summary with a
Browse-in-DM link, or `/achievements top`. Browsing is in DM (browser.py),
where `/achievements top` opens the Top view."""

from datetime import UTC, datetime

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements import browser, compare, picker, render
from nani_pix_bot.commands.achievements.common import (
    DEEP_LINK_PREFIX,
    SOMEONE_ELSE_PREFIX,
    dm_link,
    outsider_refusal,
    resolve_owner,
)
from nani_pix_bot.commands.helpers import someone_else
from nani_pix_bot.commands.helpers.rich import RichTarget, send_rich
from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.achievements import status

TOP_SIZE = 10


def _target(message: Message) -> RichTarget:
    return RichTarget(message.chat_id, thread_id=message.message_thread_id)


def _summary_markup(
    context: ContextTypes.DEFAULT_TYPE, viewer_id: int, owner_id: int, lang: str
) -> InlineKeyboardMarkup | None:
    """Browse in DM (once the bot's username is known) and, under your own
    summary, 🔍 Someone else."""
    rows = []
    url = dm_link(context, f"{DEEP_LINK_PREFIX}{owner_id}")
    if url is not None:
        rows.append([InlineKeyboardButton(i18n.t("achievements.browse_dm", lang), url=url)])
    if viewer_id == owner_id:
        rows.append([someone_else.button(SOMEONE_ELSE_PREFIX, "achievements", viewer_id, lang)])
    return InlineKeyboardMarkup(rows) if rows else None


async def _post_top(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown = render.top_table(session, status.top(session, limit=TOP_SIZE), lang, 0)
    logger.info("requested the achievements top")
    await send_rich(context.bot, _target(message), markdown)


async def _reply_unknown(message: Message, lang: str, username: str) -> None:
    logger.warning(
        "asked for achievements of unknown {target_username!r}", target_username=username
    )
    await message.reply_text(i18n.t("achievements.unknown_user", lang, username=username))


async def _open_dm(message: Message, context: ContextTypes.DEFAULT_TYPE, viewer_id: int) -> None:
    refusal = await outsider_refusal(context, viewer_id, "achievements")
    if refusal is not None:
        await message.reply_text(refusal)
        return
    args = context.args or []
    if args[:1] == ["top"]:
        await browser.open_top(message, context)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        owner_id = resolve_owner(session, viewer_id, args)
    if owner_id is None:
        await _reply_unknown(message, lang, args[0])
        return
    await browser.open_browser(message, context, browser.Browse(viewer_id, owner_id))


async def _post_summary(
    message: Message, context: ContextTypes.DEFAULT_TYPE, viewer_id: int, args: list[str]
) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        owner_id = resolve_owner(session, viewer_id, args)
    if owner_id is None:
        await _reply_unknown(message, lang, args[0])
        return
    await post_summary(message, context, viewer_id, owner_id)


async def post_summary(
    message: Message, context: ContextTypes.DEFAULT_TYPE, viewer_id: int, owner_id: int
) -> None:
    """`owner_id`'s summary as a new message in `message`'s chat and topic."""
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown = render.summary(session, owner_id, lang, datetime.now(UTC))
        target_name = players.describe_player_id(session, owner_id)
    logger.info("showed the achievements of {target}", target=target_name, target_id=owner_id)
    markup = _summary_markup(context, viewer_id, owner_id, lang)
    await send_rich(context.bot, _target(message), markdown, markup)


async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return
    if is_private_chat(update):
        await _open_dm(message, context, user.id)
        return
    bot_data = context.bot_data
    if not is_game_topic(
        update, group_chat_id=bot_data["group_chat_id"], game_topic_id=bot_data["game_topic_id"]
    ):
        return
    args = context.args or []
    if args[:1] == ["top"]:
        await _post_top(message, context)
        return
    await _post_summary(message, context, user.id, args)


# compare and picker import browser, so browser cannot register them itself.
browser.ACTIONS["c"] = compare.compare_tap
browser.ACTIONS["p"] = picker.pick_tap
