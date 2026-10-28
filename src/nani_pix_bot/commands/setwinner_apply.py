"""What /setwinner does once the winner is known, whether typed as @username
or picked from its menu (setwinner_picker.py): the refusal checks, the
re-finish of an UNSOLVED game or the close of a VOTING game's vote, and the
group announcement."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cosmetics import flair
from nani_pix_bot.services.economy import messages, settlement
from nani_pix_bot.services.game.refinish import RefinishRefusal


@dataclass(frozen=True)
class Outcome:
    """What /setwinner decided: the admin's reply, plus either a group
    announcement (UNSOLVED re-finished) or a vote to close."""

    reply: str
    announcement: str | None = None
    close_vote_for: int | None = None


@dataclass(frozen=True)
class Winner:
    player_id: int
    name: str  # "@username" as typed, or the picked player's display name


def _refused(game_id: int, refused: RefinishRefusal | None, lang: str) -> str:
    reason = (refused or RefinishRefusal.NOT_FOUND).value
    logger.warning(
        "refused /setwinner on game {target_game}: {reason}", target_game=game_id, reason=reason
    )
    return i18n.t(f"setwinner.refused.{reason}", lang, game_id=game_id)


def check_game(session: Session, game_id: int, lang: str) -> Game | str:
    """Game `game_id` when it can be re-finished at all; otherwise why not,
    as the admin's reply. Logs the refusal."""
    game = session.get(Game, game_id)
    refused = game_service.game_refusal(game)
    return game if game is not None and refused is None else _refused(game_id, refused, lang)


def check(session: Session, game_id: int, winner_id: int, bot_id: int, lang: str) -> Game | str:
    """Game `game_id` when `winner_id` can be named its winner; otherwise why
    not, as the admin's reply. Logs the refusal."""
    game = session.get(Game, game_id)
    refused = game_service.refinish_refusal(game, winner_id=winner_id, bot_id=bot_id)
    return game if game is not None and refused is None else _refused(game_id, refused, lang)


def apply(session: Session, game: Game, winner: Winner, lang: str) -> Outcome:
    """Name `winner` the winner of `game`, which passed `check`."""
    done = i18n.t("setwinner.done", lang, winner=winner.name, game_id=game.id)
    if game.status == GameStatus.VOTING:
        logger.info(
            "closing game {target_game}'s vote with an admin-named winner",
            target_game=game.id,
            game_id=game.id,
        )
        return Outcome(done, close_vote_for=winner.player_id)
    return Outcome(done, announcement=_refinish(session, game, winner, lang))


def _refinish(session: Session, game: Game, winner: Winner, lang: str) -> str:
    """Re-finish an UNSOLVED game and return the group announcement."""
    game_service.refinish(session, game, winner_id=winner.player_id)
    name = flair.named(session, winner.player_id, winner.name)
    earnings = settlement.settle_disputed_win(session, game, winner.player_id)
    text = i18n.t(
        "setwinner.announcement",
        lang,
        game_id=game.id,
        winner=name,
        title=game_service.display_title(game, lang),
    )
    return text + messages.earnings_suffix(session, game, earnings, lang, player_name=name)


async def deliver(
    update: Update, context: ContextTypes.DEFAULT_TYPE, game_id: int, outcome: Outcome
) -> None:
    """Once `apply`'s session has committed: close the vote, or tell the group."""
    if outcome.close_vote_for is not None:
        await _close_vote(update, context, game_id, outcome.close_vote_for)
    elif outcome.announcement is not None:
        await _announce(context, game_id, outcome.announcement)


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
