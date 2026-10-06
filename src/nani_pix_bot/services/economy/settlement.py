"""What a game's ending pays out (issue #252): every money side effect of a
game ending unsolved, or being won by a vote or an admin, in one place so
the /guess, inactivity, timeout and vote-close paths can't drift apart.
Returns caption text or Earnings for the caller to post."""

from dataclasses import replace

from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import bounty, earning
from nani_pix_bot.services.economy.earning import Earnings


def settle_unsolved(session: Session, game: Game, lang: str) -> str:
    """Refund the bounty pot and, for HARD MODE, pay clue cashback (issue #255);
    returns the caption lines to append."""
    note = bounty.refund_note(session, game.id, lang)
    cashback = earning.award_cashback(session, game)
    if cashback:
        note += "\n" + i18n.t("economy.cashback", lang, amount=cashback)
    return note


def settle_disputed_win(session: Session, game: Game, winner_id: int) -> Earnings:
    """The win payout for a winner decided by a vote or an admin (/setwinner),
    plus the compensation bonus. For an admin re-finish the pot was already
    refunded when the game ended, so there is no bounty to pay."""
    earnings = earning.award_win(session, game, winner_id=winner_id)
    return replace(
        earnings, compensation=earning.award_compensation(session, game, winner_id=winner_id)
    )
