"""The /skip command — hands off or opens the turn to start the next
game. See MECHANICS.md's "Turn handoff" section. `/skip @user` hands the turn
to that player at once; `/skip` alone shows a menu (skip_picker.py) whose
first row opens the turn to everyone."""

from loguru import logger
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import skip_picker
from nani_pix_bot.commands.game_flow.skip_turn import hand_turn, refusal_key
from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cosmetics import flair


async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        refusal = refusal_key(session, user.id)
        if refusal is not None:
            await message.reply_text(i18n.t(refusal, lang))
            return
        if not context.args:
            text, keyboard = skip_picker.menu_view(session, user.id, context.bot.id, 0)
            logger.info("opened the /skip menu")
            await message.reply_text(text, reply_markup=keyboard)
            return
        target_label = await _pass_turn(message, context, session, lang, context.args[0])
        if target_label is None:
            return
    # Block closed and committed above — the turn-state change is durable
    # now regardless of whether the confirmation below actually reaches
    # the group (see jobs/timers/current_image.py's post_current_image
    # docstring for the general principle).
    try:
        await message.reply_text(i18n.t("skip.passed", lang, username=target_label))
    except TelegramError as exc:
        logger.warning("failed to send the /skip confirmation: {error}", error=exc)


async def _pass_turn(
    message, context: ContextTypes.DEFAULT_TYPE, session, lang: str, raw_username: str
) -> str | None:
    """Returns the target's label (@username plus flair) on success, or None for an unknown
    username — that failure reply goes out here, inline: no mutation
    happened on this path, so there's nothing for a timeout to roll
    back (unlike the successful outcome, whose confirmation the
    caller sends only after this whole session commits)."""
    target_username = raw_username.lstrip("@")
    target = players.find_player_by_username(session, target_username)
    if target is None:
        logger.warning(
            "tried /skip to unknown username {target_username!r}",
            target_username=target_username,
        )
        # Same handle-less window as correct.py's identical lookup — see
        # the comment there for why the fallback drops the sentence
        # rather than rendering a bare "@".
        bot_username = context.bot_data.get("bot_username")
        key = "skip.unknown_username" if bot_username else "skip.unknown_username_no_handle"
        await message.reply_text(
            i18n.t(key, lang, username=target_username, bot_username=bot_username)
        )
        return None
    if target.telegram_user_id == context.bot.id:
        # Same guard as correct.py's — the bot gets a real `players` row
        # once it starts its first game (issue #159), so it's otherwise
        # addressable by username here too. Lower severity than
        # /correct's (a self-designated bot turn self-heals via the 12h
        # turn-expiry timer), but still not a turn worth handing to it
        # explicitly.
        logger.warning(
            "tried /skip to the bot itself (@{target_username})",
            target_username=target_username,
        )
        await message.reply_text(i18n.t("skip.cannot_target_bot", lang))
        return None

    hand_turn(context, session, target.telegram_user_id)
    return flair.named(session, target.telegram_user_id, f"@{target_username}")
