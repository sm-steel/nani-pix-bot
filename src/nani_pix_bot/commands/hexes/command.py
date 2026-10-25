"""/hex — cast a hex on another player, in a DM or the game topic.

- `/hex`: the season's hexes, with a picker of players to hex under them.
- `/hex @user`: the kind picker for that player.
- `/hex @user <kind>`: casts at once."""

from loguru import logger
from telegram import Message, Update, User
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands.hexes import views
from nani_pix_bot.commands.hexes.casting import Cast, cast_hex
from nani_pix_bot.commands.hexes.common import in_scope, language, parse_kind
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings


async def _start(message: Message, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        text, keyboard = views.start_view(session, user.id, 0, lang)
    logger.info("opened the hex picker", has_targets=keyboard is not None)
    await message.reply_text(text, reply_markup=keyboard)


async def _usage(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.warning("malformed /hex: {args!r}", args=context.args)
    await message.reply_text(i18n.t("hex.usage", language(context)))


async def _kinds(
    message: Message, context: ContextTypes.DEFAULT_TYPE, user: User, target_id: int
) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        text, keyboard = views.kind_view(session, user.id, target_id, lang)
        target = players.describe_player_id(session, target_id)
    logger.info("opened the hex kinds for {target}", target=target, target_id=target_id)
    await message.reply_text(text, reply_markup=keyboard)


async def _cast(
    message: Message, context: ContextTypes.DEFAULT_TYPE, user: User, cast: Cast
) -> None:
    reply = await cast_hex(context, user, cast)
    # In the topic the notice speaks for itself; a DM gets the confirmation.
    if not reply.ok or message.chat.type == ChatType.PRIVATE:
        await message.reply_text(reply.text)


def _target_id(context: ContextTypes.DEFAULT_TYPE, username: str) -> tuple[int | None, str]:
    """The player called @username, or None, and the reply language."""
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        target = players.find_player_by_username(session, username)
        return (None if target is None else target.telegram_user_id), lang


async def hex_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    if message is None or user is None or not in_scope(update, context):
        return
    args = context.args or []
    if not args:
        await _start(message, context, user)
        return
    kind = parse_kind(args[1]) if len(args) == 2 else None
    if len(args) > 2 or (len(args) == 2 and kind is None):
        await _usage(message, context)
        return
    username = args[0].lstrip("@")
    target_id, lang = _target_id(context, username)
    if target_id is None:
        logger.warning(
            "tried /hex on unknown username {target_username!r}", target_username=username
        )
        await message.reply_text(i18n.t("hex.unknown_user", lang, username=username))
    elif kind is None:
        await _kinds(message, context, user, target_id)
    else:
        await _cast(message, context, user, Cast(target_id, kind))
