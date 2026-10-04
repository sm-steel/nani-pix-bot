"""A game's bounty pot: currency players put up for whoever solves it.
The pot is a ledger party (CurrencyParty.POT, keyed by game_id) — its
balance is derived from currency_transfers, never stored. Winner takes
all (pay_out); if the game ends any other way every contribution is
refunded, each naming its contribution via reverses_id (refund_pot), so a
finished game's pot always nets to zero."""

import enum

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import CurrencyParty, CurrencyReason, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import wallet

BOUNTY_MIN = 5


class BountyRefusal(enum.StrEnum):
    NOT_OPEN = "not_open"
    BELOW_MIN = "below_min"
    INSUFFICIENT = "insufficient"


class BountyRefusedError(Exception):
    def __init__(self, refusal: BountyRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


def _sum(session: Session, *conditions) -> int:
    stmt = select(func.coalesce(func.sum(CurrencyTransfer.amount), 0)).where(*conditions)
    return int(session.scalar(stmt) or 0)


def pot_balance(session: Session, game_id: int) -> int:
    into = _sum(
        session,
        CurrencyTransfer.game_id == game_id,
        CurrencyTransfer.to_type == CurrencyParty.POT,
    )
    out = _sum(
        session,
        CurrencyTransfer.game_id == game_id,
        CurrencyTransfer.from_type == CurrencyParty.POT,
    )
    return into - out


def _open_for(game: Game, player: Player) -> bool:
    if game.status == GameStatus.ACTIVE:
        return True
    return game.status == GameStatus.SETUP and player.telegram_user_id == game.starter_id


def contribute(session: Session, game: Game, player: Player, amount: int) -> CurrencyTransfer:
    if not _open_for(game, player):
        raise BountyRefusedError(BountyRefusal.NOT_OPEN)
    if amount < BOUNTY_MIN:
        raise BountyRefusedError(BountyRefusal.BELOW_MIN)
    try:
        row = wallet.transfer(
            session,
            wallet.Party.of(player),
            wallet.Party.pot(),
            amount,
            wallet.LedgerEntry(CurrencyReason.BOUNTY, game_id=game.id),
        )
    except wallet.InsufficientCurrencyError as error:
        raise BountyRefusedError(BountyRefusal.INSUFFICIENT) from error
    session.flush()
    logger.info("Player {} added {} to game {}'s bounty", player.telegram_user_id, amount, game.id)
    return row


def pay_out(session: Session, game: Game, winner: Player) -> int:
    session.flush()
    amount = pot_balance(session, game.id)
    if amount <= 0:
        return 0
    wallet.transfer(
        session,
        wallet.Party.pot(),
        wallet.Party.of(winner),
        amount,
        wallet.LedgerEntry(CurrencyReason.BOUNTY_WIN, game_id=game.id),
    )
    logger.info("Game {}: bounty of {} paid to {}", game.id, amount, winner.telegram_user_id)
    return amount


def _unrefunded_contributions(session: Session, game_id: int) -> list[CurrencyTransfer]:
    refunded = select(CurrencyTransfer.reverses_id).where(CurrencyTransfer.reverses_id.is_not(None))
    stmt = select(CurrencyTransfer).where(
        CurrencyTransfer.game_id == game_id,
        CurrencyTransfer.to_type == CurrencyParty.POT,
        CurrencyTransfer.id.not_in(refunded),
    )
    return list(session.scalars(stmt))


def refund_pot(session: Session, game_id: int) -> int:
    """Return every unrefunded contribution — unless the pot was already
    paid out (balance 0), in which case there is nothing to give back."""
    session.flush()
    if pot_balance(session, game_id) <= 0:
        return 0
    total = 0
    for contribution in _unrefunded_contributions(session, game_id):
        contributor = session.get(Player, contribution.from_player_id)
        if contributor is None:
            continue
        wallet.transfer(
            session,
            wallet.Party.pot(),
            wallet.Party.of(contributor),
            contribution.amount,
            wallet.LedgerEntry(CurrencyReason.REFUND, game_id=game_id, reverses_id=contribution.id),
        )
        total += contribution.amount
    logger.info("Game {}: bounty of {} refunded to contributors", game_id, total)
    return total


def refund_note(session: Session, game_id: int, lang: str) -> str:
    """Refunds the pot for a game that just ended unsolved and returns the
    line to append to its reveal caption ("" when the pot was empty)."""
    refunded = refund_pot(session, game_id)
    if refunded <= 0:
        return ""
    return "\n" + i18n.t("economy.bounty_refunded", lang, amount=refunded)
