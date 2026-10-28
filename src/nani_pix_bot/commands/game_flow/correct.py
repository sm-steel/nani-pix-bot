"""The /correct command — the author-override path when the fuzzy
matcher misses a genuinely correct guess. See MECHANICS.md's "Winning"
section. `/correct @user` awards the win at once; `/correct` alone opens a
picker of this round's guessers (correct_picker.py)."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Message, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import correct_picker
from nani_pix_bot.commands.game_flow.correct_award import (
    Reveal,
    Winner,
    check_game,
    commit_win,
    post_win,
)
from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, players, settings


async def correct_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if (
        message is None
        or user is None
        or not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id)
    ):
        return

    with session_scope(context.bot_data["session_factory"]) as session:
        reveal = await _reply_or_award(session, message, context, user.id)
    # Block closed and committed above — the win is durable now regardless
    # of whether the reveal below actually reaches the group.
    if reveal is not None:
        await post_win(context, update, reveal)


async def _reply_or_award(
    session: Session, message: Message, context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> Reveal | None:
    """Reply to /correct, or commit the win it awards and return its reveal."""
    lang = settings.get_language(session)
    args = context.args or []
    if len(args) > 1:
        logger.warning("malformed /correct: {args!r}", args=args)
        await message.reply_text(i18n.t("correct.usage", lang))
        return None
    game = check_game(session, user_id)
    if not isinstance(game, Game):
        if game is not None:
            await message.reply_text(i18n.t(game, lang))
        return None
    if not args:
        text, keyboard = correct_picker.players_view(session, game, context.bot.id, 0)
        logger.info("opened the /correct picker", has_targets=keyboard is not None)
        await message.reply_text(text, reply_markup=keyboard)
        return None
    return await _award_named(session, message, context, game, args[0].lstrip("@"))


async def _award_named(
    session: Session,
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    game: Game,
    target_username: str,
) -> Reveal | None:
    lang = settings.get_language(session)
    target = await _resolve_target_player(session, message, context, target_username, lang)
    if target is None:
        return None
    if target.telegram_user_id == game.starter_id:
        logger.warning("starter tried to /correct themselves", game_id=game.id)
        await message.reply_text(i18n.t("correct.cannot_target_self", lang))
        return None
    winner = Winner(target.telegram_user_id, f"@{target_username}")
    return commit_win(session, context, game, winner, lang)


async def _resolve_target_player(
    session: Session,
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    target_username: str,
    lang: str,
) -> Player | None:
    """Takes `context` (rather than the `game`, whose id used to go in the
    warning below) because the not-found reply names the bot's own handle
    so the target can be told exactly where to write — see
    `correct.unknown_username`. The game id is still on the surrounding
    lifecycle log lines, and this now matches skip.py's identical
    lookup/log/reply for the same failure."""
    target = players.find_player_by_username(session, target_username)
    if target is None:
        logger.warning(
            "tried /correct on unknown username {target_username!r}",
            target_username=target_username,
        )
        # bot_data["bot_username"] is written by app.py's _post_init, so
        # it is absent until the first getMe answers (and in tests). The
        # old "" default put a dangling "@" at the end of the sentence,
        # which reads as a bug; dropping the handle drops the sentence
        # with it (see skip.py, which has the same window).
        bot_username = context.bot_data.get("bot_username")
        key = "correct.unknown_username" if bot_username else "correct.unknown_username_no_handle"
        await message.reply_text(
            i18n.t(key, lang, username=target_username, bot_username=bot_username)
        )
        return None
    if target.telegram_user_id == context.bot.id:
        # The bot gets its own real `players` row once it starts its
        # first game (issue #159's autostart/overthrow) — addressable by
        # username with no special-casing otherwise, which would award
        # the bot itself a win and a leaderboard entry. Unlike an unknown
        # username, the player genuinely exists, so a dedicated reply
        # rather than reusing correct.unknown_username.
        logger.warning(
            "tried /correct on the bot itself (@{target_username})",
            target_username=target_username,
        )
        await message.reply_text(i18n.t("correct.cannot_target_bot", lang))
        return None
    return target
