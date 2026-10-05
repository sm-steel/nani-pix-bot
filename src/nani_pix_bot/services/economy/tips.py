"""Player-to-player tips (`/tip`): a plain transfer between two players,
reason TIP, tied to no game."""

import enum

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import CurrencyReason
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import players
from nani_pix_bot.services.economy import wallet

TIP_MIN = 1


class TipRefusal(enum.StrEnum):
    SELF = "self"
    BELOW_MIN = "below_min"
    INSUFFICIENT = "insufficient"


class TipRefusedError(Exception):
    def __init__(self, refusal: TipRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


def tip(session: Session, sender: Player, recipient: Player, amount: int) -> CurrencyTransfer:
    """One player -> player transfer, reason TIP, no game."""
    if sender.telegram_user_id == recipient.telegram_user_id:
        raise TipRefusedError(TipRefusal.SELF)
    if amount < TIP_MIN:
        raise TipRefusedError(TipRefusal.BELOW_MIN)
    try:
        row = wallet.transfer(
            session,
            wallet.Party.of(sender),
            wallet.Party.of(recipient),
            amount,
            wallet.LedgerEntry(CurrencyReason.TIP),
        )
    except wallet.InsufficientCurrencyError as error:
        raise TipRefusedError(TipRefusal.INSUFFICIENT) from error
    session.flush()
    logger.info(
        "tipped {recipient} {amount} 💠",
        recipient=players.describe_player_id(session, recipient.telegram_user_id),
        recipient_id=recipient.telegram_user_id,
        amount=amount,
    )
    return row
