"""Admin re-finish (issue #253): name the winner of a game that already
ended UNSOLVED, because the matcher (or the group) got it wrong. The result
is a normal win, apart from two things: the turn stays where it is, since a
newer game may already be running, and the game keeps its original ending
time. A VOTING game is the command's job: it closes the vote with that winner."""

import enum

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, WinMethod
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import players
from nani_pix_bot.services.game import state
from nani_pix_bot.services.game.hard_mode import HARD_MODE_WIN_AWARD


class RefinishRefusal(enum.StrEnum):
    NOT_FOUND = "not_found"
    NOT_UNSOLVED = "not_unsolved"
    BOT = "bot"
    STARTER = "starter"


def refinish_refusal(game: Game | None, *, winner_id: int, bot_id: int) -> RefinishRefusal | None:
    if game is None:
        return RefinishRefusal.NOT_FOUND
    if game.status not in (GameStatus.UNSOLVED, GameStatus.VOTING):
        return RefinishRefusal.NOT_UNSOLVED
    if winner_id == bot_id:
        return RefinishRefusal.BOT
    if winner_id == game.starter_id:
        return RefinishRefusal.STARTER
    return None


def refinish(session: Session, game: Game, *, winner_id: int) -> None:
    if game.status != GameStatus.UNSOLVED:
        msg = f"refinish on game {game.id} with status {game.status}"
        raise ValueError(msg)
    logger.info(
        "re-finished by an admin — {winner} named the winner",
        winner=players.describe_player_id(session, winner_id),
        winner_id=winner_id,
        game_id=game.id,
    )
    award = HARD_MODE_WIN_AWARD if game.hard_mode else 1
    terms = state.WinTerms(award=award, how=WinMethod.SETWINNER)
    state._record_win(session, game, winner_id=winner_id, terms=terms)
