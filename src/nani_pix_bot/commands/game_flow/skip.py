"""The /skip command — hands off or opens the turn to start the next
game. See MECHANICS.md's "Turn handoff" section."""

from loguru import logger
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings


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
        if game_service.active_or_setup_game(session) is not None:
            logger.warning("{} tried /skip while a game is running", user.id)
            await message.reply_text(i18n.t("skip.game_running", lang))
            return

        turn_state_check = game_service.get_turn_state(session)
        current = turn_state_check.next_starter_id if turn_state_check is not None else None
        if current is not None and current != user.id:
            logger.warning("{} tried /skip out of turn (designated: {})", user.id, current)
            await message.reply_text(i18n.t("skip.not_your_turn", lang))
            return

        turn_state: TurnState | None = None
        if not context.args:
            turn_state = _open_turn(context, session)
            reply_key, reply_kwargs = "skip.opened", {}
        else:
            target_username = await _pass_turn(message, context, session, lang, context.args[0])
            if target_username is None:
                return
            reply_key, reply_kwargs = "skip.passed", {"username": target_username}
    # Block closed and committed above — the turn-state change is durable
    # now regardless of whether the confirmation below actually reaches
    # the group (see jobs/timers/current_image.py's post_current_image
    # docstring for the general principle).
    if turn_state is not None:
        timeout_module.schedule_idle_autostart(context.job_queue, turn_state)
    try:
        await message.reply_text(i18n.t(reply_key, lang, **reply_kwargs))
    except TelegramError as exc:
        logger.warning("Failed to send the /skip confirmation: {}", exc)


def _open_turn(context: ContextTypes.DEFAULT_TYPE, session) -> TurnState:
    turn_state = game_service.set_next_starter(session, None)
    timeout_module.cancel_turn_timers(context.job_queue)
    return turn_state


async def _pass_turn(
    message, context: ContextTypes.DEFAULT_TYPE, session, lang: str, raw_username: str
) -> str | None:
    """Returns the resolved username on success, or None for an unknown
    username — that failure reply goes out here, inline: no mutation
    happened on this path, so there's nothing for a timeout to roll
    back (unlike the two successful outcomes, whose confirmation the
    caller sends only after this whole session commits)."""
    target_username = raw_username.lstrip("@")
    target = players.find_player_by_username(session, target_username)
    if target is None:
        logger.warning("/skip: unknown username {!r}", target_username)
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
        logger.warning("/skip: rejected targeting the bot itself ({!r})", target_username)
        await message.reply_text(i18n.t("skip.cannot_target_bot", lang))
        return None

    turn_state = game_service.set_next_starter(session, target.telegram_user_id)
    timeout_module.cancel_idle_autostart(context.job_queue)
    timeout_module.schedule_turn_timers(context.job_queue, turn_state)
    return target_username
