"""/setwinner <game id> @user — admin-only (DM) re-finish (issue #253). An
UNSOLVED game is marked won and the group is told; a VOTING game's vote is
closed with that winner (the vote's own winner tail, overthrow included)."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.economy import messages, settlement

# /setwinner <game id> @username
_ARG_COUNT = 2


@dataclass(frozen=True)
class _Outcome:
    """What _resolve_and_apply decided: the admin's reply, plus either a
    group announcement (UNSOLVED re-finished) or a vote to close."""

    reply: str
    announcement: str | None = None
    close_vote_for: int | None = None


def _parse(args: list[str]) -> tuple[int, str] | None:
    if len(args) != _ARG_COUNT:
        return None
    try:
        return int(args[0].lstrip("#")), args[1].lstrip("@")
    except ValueError:
        return None


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
    outcome = _resolve_and_apply(session_factory, context.bot.id, game_id, username, lang)
    if outcome.close_vote_for is not None:
        await _close_vote(update, context, game_id, outcome.close_vote_for)
    elif outcome.announcement is not None:
        await _announce(context, game_id, outcome.announcement)
    await message.reply_text(outcome.reply)


async def _close_vote(
    update: Update, context: ContextTypes.DEFAULT_TYPE, game_id: int, winner_id: int
) -> None:
    session_factory = context.bot_data["session_factory"]
    final = await timeout_module.finalize_vote(
        context, session_factory, game_id, forced_winner_id=winner_id
    )
    if final is None:
        return
    context.application.create_task(
        timeout_module.maybe_overthrow(
            context, session_factory, winner_id=final.winner_id, winner_name=final.winner_name
        ),
        update=update,
    )


async def _announce(context: ContextTypes.DEFAULT_TYPE, game_id: int, text: str) -> None:
    try:
        await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=text,
        )
    except TelegramError as exc:
        logger.warning("failed to announce the re-finish: {error}", error=exc, game_id=game_id)


def _resolve_and_apply(
    session_factory: sessionmaker[Session], bot_id: int, game_id: int, username: str, lang: str
) -> _Outcome:
    with session_scope(session_factory) as session:
        game = session.get(Game, game_id)
        target = players.find_player_by_username(session, username)
        if target is None:
            logger.warning(
                "tried /setwinner on unknown username {target_username!r}",
                target_username=username,
            )
            return _Outcome(i18n.t("setwinner.unknown_user", lang, username=username))
        refusal = game_service.refinish_refusal(
            game, winner_id=target.telegram_user_id, bot_id=bot_id
        )
        if refusal is not None or game is None:
            reason = refusal.value if refusal is not None else "not_found"
            logger.warning(
                "refused /setwinner on game {target_game}: {reason}",
                target_game=game_id,
                reason=reason,
            )
            return _Outcome(i18n.t(f"setwinner.refused.{reason}", lang, game_id=game_id))
        winner = f"@{username}"
        done = i18n.t("setwinner.done", lang, winner=winner, game_id=game_id)
        if game.status == GameStatus.VOTING:
            logger.info(
                "closing game {target_game}'s vote with an admin-named winner",
                target_game=game_id,
                game_id=game_id,
            )
            return _Outcome(done, close_vote_for=target.telegram_user_id)
        return _Outcome(done, announcement=_refinish(session, game, target, winner, lang))


def _refinish(session: Session, game: Game, target: Player, winner: str, lang: str) -> str:
    """Re-finish an UNSOLVED game and return the group announcement."""
    game_service.refinish(session, game, winner_id=target.telegram_user_id)
    earnings = settlement.settle_refinish(session, game, target.telegram_user_id)
    text = i18n.t(
        "setwinner.announcement",
        lang,
        game_id=game.id,
        winner=winner,
        title=game_service.display_title(game, lang),
    )
    return text + messages.earnings_suffix(session, game, earnings, lang, player_name=winner)
