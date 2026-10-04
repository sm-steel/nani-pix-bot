"""The /tip command — sends currency to another player, in DM or the game
topic. See services/economy/tips.py for the rules."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.economy import tips


def _parse(args: list[str] | None) -> tuple[str, int] | None:
    """`(username, amount)` from `/tip @user <N>`, or None for any other shape."""
    if not args or len(args) != 2:
        return None
    try:
        return args[0].lstrip("@"), int(args[1])
    except ValueError:
        return None


def _unknown_user_reply(context: ContextTypes.DEFAULT_TYPE, username: str, lang: str) -> str:
    # bot_data["bot_username"] is absent until the first getMe answers (and in
    # tests); without it the "write to the bot" hint is dropped, as in /correct.
    bot_username = context.bot_data.get("bot_username")
    key = "tip.unknown_user_hint" if bot_username else "tip.unknown_user"
    return i18n.t(key, lang, username=username, bot_username=bot_username)


async def tip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return
    in_topic = is_game_topic(
        update,
        group_chat_id=context.bot_data["group_chat_id"],
        game_topic_id=context.bot_data["game_topic_id"],
    )
    if not (in_topic or is_private_chat(update)):
        return

    session_factory = context.bot_data["session_factory"]
    parsed = _parse(context.args)
    refusal: tips.TipRefusal | None = None
    try:
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
            if parsed is None:
                reply = i18n.t("tip.usage", lang)
            else:
                username, amount = parsed
                recipient = players.find_player_by_username(session, username)
                if recipient is None:
                    logger.warning("{} tried /tip to unknown username {!r}", user.id, username)
                    reply = _unknown_user_reply(context, username, lang)
                elif recipient.telegram_user_id == context.bot.id:
                    logger.warning("{} tried to /tip the bot", user.id)
                    reply = i18n.t("tip.cannot_tip_bot", lang)
                else:
                    sender = players.get_or_create_player(session, user.id, username=user.username)
                    tips.tip(session, sender, recipient, amount)
                    reply = i18n.t(
                        "tip.done",
                        lang,
                        sender=user.full_name,
                        recipient=username,
                        amount=amount,
                    )
    except tips.TipRefusedError as error:
        refusal = error.refusal
    if refusal is not None:
        reply = _refusal_reply(session_factory, user.id, refusal)
    await message.reply_text(reply)


def _refusal_reply(session_factory, user_id: int, refusal: tips.TipRefusal) -> str:
    """The reply for a refused tip; its transaction already rolled back, so
    the balance is read in a fresh scope."""
    logger.warning("{}'s /tip was refused: {}", user_id, refusal.value)
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        player = session.get(Player, user_id)
        balance = player.currency if player is not None else 0
    return i18n.t(f"tip.refusal.{refusal.value}", lang, minimum=tips.TIP_MIN, balance=balance)
