"""What a game's ending pays out (issue #252): every money side effect of a
game ending unsolved, or being won by a vote or an admin, in one place so
the /guess, inactivity, timeout and vote-close paths can't drift apart.
Returns caption text or Earnings for the caller to post."""

from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.services.economy import bounty, earning
from nani_pix_bot.services.economy.earning import Earnings


def settle_unsolved(session: Session, game: Game, lang: str) -> str:
    """Refund the bounty pot; returns the caption lines to append."""
    return bounty.refund_note(session, game.id, lang)


def settle_vote_win(session: Session, game: Game, winner_id: int) -> Earnings:
    """The regular win payout for a winner decided by a vote or an admin."""
    return earning.award_win(session, game, winner_id=winner_id)
