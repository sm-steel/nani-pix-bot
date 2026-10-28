"""/setwinner <game id> [@user] — admin-only (DM) re-finish (issue #253). An
UNSOLVED game is marked won and the group is told; a VOTING game's vote is
closed with that winner (the vote's own winner tail, overthrow included).
Without the @user, a menu of the game's guessers (setwinner_picker.py)."""

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import setwinner_apply, setwinner_picker
from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.commands.setwinner_apply import Outcome, Winner
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings

# /setwinner <game id> [@username]
_MAX_ARGS = 2


def _parse(args: list[str]) -> tuple[int, str | None] | None:
    if not 1 <= len(args) <= _MAX_ARGS:
        return None
    try:
        game_id = int(args[0].lstrip("#"))
    except ValueError:
        return None
    return game_id, args[1].lstrip("@") if len(args) == _MAX_ARGS else None


async def setwinner_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
    if not await is_group_admin(context.bot, context.bot_data["group_chat_id"], user.id):
        logger.warning("non-admin tried /setwinner")
        await message.reply_text(i18n.t("commands.admins_only", lang))
        return
    parsed = _parse(context.args or [])
    if parsed is None:
        logger.info(
            "sent /setwinner with bad args {args!r} — replied with usage", args=context.args
        )
        await message.reply_text(i18n.t("setwinner.usage", lang))
        return
    game_id, username = parsed
    if username is None:
        with session_scope(session_factory) as session:
            text, keyboard = setwinner_picker.open_view(session, user.id, game_id, context.bot.id)
        await message.reply_text(text, reply_markup=keyboard)
        return
    outcome = _resolve_and_apply(session_factory, context.bot.id, game_id, username, lang)
    await setwinner_apply.deliver(update, context, game_id, outcome)
    await message.reply_text(outcome.reply)


def _resolve_and_apply(
    session_factory: sessionmaker[Session], bot_id: int, game_id: int, username: str, lang: str
) -> Outcome:
    with session_scope(session_factory) as session:
        target = players.find_player_by_username(session, username)
        if target is None:
            logger.warning(
                "tried /setwinner on unknown username {target_username!r}",
                target_username=username,
            )
            return Outcome(i18n.t("setwinner.unknown_user", lang, username=username))
        winner_id = target.telegram_user_id
        game = setwinner_apply.check(session, game_id, winner_id, bot_id, lang)
        if isinstance(game, str):
            return Outcome(game)
        return setwinner_apply.apply(session, game, Winner(winner_id, f"@{username}"), lang)
