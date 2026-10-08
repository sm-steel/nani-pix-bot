"""/start and /help — see MECHANICS.md's overview and ARCHITECTURE.md's
command/topic model."""

from loguru import logger
from telegram import Message, Update, User
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.browser import Browse, open_browser
from nani_pix_bot.commands.achievements.common import DEEP_LINK_PREFIX, MAX_ID, outsider_refusal
from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.commands.shop import open_shop
from nani_pix_bot.commands.standings_dm import (
    RECENT_PAYLOAD,
    RULES_PAYLOAD,
    open_recent,
    open_rules,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings


def _achievements_owner(args: list[str] | None) -> int | None:
    """The owner id from `/start ach_<id>`, or None. The `len` check also keeps
    the existing tests' MagicMock `context.args` (len 0) out of this path."""
    if not args or len(args) != 1 or not args[0].startswith(DEEP_LINK_PREFIX):
        return None
    raw = args[0].removeprefix(DEEP_LINK_PREFIX)
    if not raw.isdecimal() or int(raw) > MAX_ID:
        logger.warning("ignored a malformed achievements deep link {arg!r}", arg=args[0])
        return None
    return int(raw)


_STANDINGS_VIEWS = {(RULES_PAYLOAD,): open_rules, (RECENT_PAYLOAD,): open_recent}


async def _members_only(
    message: Message, context: ContextTypes.DEFAULT_TYPE, user_id: int, what: str
) -> bool:
    """True when the deep link may open; otherwise the refusal is sent."""
    refusal = await outsider_refusal(context, user_id, what)
    if refusal is not None:
        await message.reply_text(refusal)
    return refusal is None


async def _open_deep_link(message: Message, context: ContextTypes.DEFAULT_TYPE, user: User) -> bool:
    """Opens what `/start <payload>` points at; False when it carried none."""
    owner_id = _achievements_owner(context.args)
    if owner_id is not None:
        if await _members_only(message, context, user.id, "achievements deep link"):
            logger.info("opened achievements via the /start deep link")
            await open_browser(message, context, Browse(user.id, owner_id))
        return True
    standings_view = _STANDINGS_VIEWS.get(tuple(context.args or ()))
    if standings_view is not None:
        if await _members_only(message, context, user.id, "standings deep link"):
            await standings_view(message, context)
        return True
    if context.args == ["shop"]:
        logger.info("opened the shop via the /start deep link")
        await open_shop(message, context, user)
        return True
    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    if await _open_deep_link(message, context, user):
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
    logger.info("sent /start")
    await message.reply_text(i18n.t("onboarding.start", lang))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

    if is_private_chat(update):
        logger.info("sent /help in DM")
        await message.reply_text(i18n.t("onboarding.help", lang))
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    try:
        await context.bot.send_message(chat_id=user.id, text=i18n.t("onboarding.help", lang))
        logger.info("sent /help in the game topic — help sent by DM")
    except Forbidden:
        logger.warning("couldn't DM /help — they haven't started the bot")
        # bot_data["bot_username"] is written by app.py's _post_init, so it
        # is absent until the first getMe answers (and in tests). The old
        # "" default put a dangling "@" in the middle of the sentence,
        # which reads as a bug; dropping the handle drops the mention
        # clause instead (see correct.py/skip.py, #81, same window).
        bot_username = context.bot_data.get("bot_username")
        key = "onboarding.help_dm_failed" if bot_username else "onboarding.help_dm_failed_no_handle"
        await message.reply_text(i18n.t(key, lang, bot_username=bot_username))
