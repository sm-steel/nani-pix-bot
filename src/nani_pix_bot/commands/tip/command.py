"""/tip @user <amount> tips at once; /tip or /tip <amount> opens the player picker."""

from loguru import logger
from telegram import Message, Update, User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.commands.tip import views
from nani_pix_bot.commands.tip.sending import TipRequest, refusal_reply, tip_reply
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.economy import tips


def _amount(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def _parse(args: list[str]) -> TipRequest | None:
    """`/tip <N>` or `/tip @user <N>`; None for any other shape."""
    amount = _amount(args[-1]) if args else None
    if amount is None or len(args) > 2:
        return None
    return TipRequest(amount, username=args[0].lstrip("@") if len(args) == 2 else None)


def _in_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    in_topic = is_game_topic(
        update,
        group_chat_id=context.bot_data["group_chat_id"],
        game_topic_id=context.bot_data["game_topic_id"],
    )
    return in_topic or is_private_chat(update)


async def _open_picker(
    message: Message, context: ContextTypes.DEFAULT_TYPE, user: User, amount: int | None
) -> None:
    """The player picker; `amount` None leaves it to the amount buttons."""
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        tipper = session.get(Player, user.id)
        balance = 0 if tipper is None else tipper.currency
        refusal = None if amount is None else tips.amount_refusal(amount, balance)
        if refusal is None:
            at = views.picker(user.id, amount or 0)
            view = views.players_view(session, at, context.bot.id, 0)
    if refusal is not None:
        await message.reply_text(refusal_reply(session_factory, user.id, refusal))
        return
    text, keyboard = view
    logger.info("opened the /tip picker", amount=amount, has_targets=keyboard is not None)
    await message.reply_text(text, reply_markup=keyboard)


async def tip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    if message is None or user is None or not _in_scope(update, context):
        return
    args = context.args or []
    request = _parse(args)
    if not args:
        await _open_picker(message, context, user, None)
    elif request is None:
        logger.warning("malformed /tip: {args!r}", args=context.args)
        with session_scope(context.bot_data["session_factory"]) as session:
            reply = i18n.t("tip.usage", settings.get_language(session))
        await message.reply_text(reply)
    elif request.username is None:
        await _open_picker(message, context, user, request.amount)
    else:
        await message.reply_text(tip_reply(context, user, request))
