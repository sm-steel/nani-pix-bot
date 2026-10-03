"""The only code that changes a 💠 balance. Every change updates
`Player.pixels` *and* adds a `PixelTransaction` row in the caller's
session, so they commit (or roll back) together."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import PixelReason
from nani_pix_bot.models.pixel_transaction import PixelTransaction
from nani_pix_bot.models.player import Player


@dataclass(frozen=True)
class LedgerEntry:
    """What a balance change is for — the ledger row's non-amount fields."""

    reason: PixelReason
    game_id: int | None = None
    detail: str | None = None


class InsufficientPixelsError(Exception):
    def __init__(self, balance: int, amount: int) -> None:
        super().__init__(f"balance {balance} < {amount}")
        self.balance = balance
        self.amount = amount


def _record(
    session: Session,
    player: Player,
    signed_amount: int,
    entry: LedgerEntry,
) -> PixelTransaction:
    player.pixels += signed_amount
    tx = PixelTransaction(
        player_id=player.telegram_user_id,
        game_id=entry.game_id,
        amount=signed_amount,
        reason=entry.reason,
        detail=entry.detail,
    )
    session.add(tx)
    logger.debug(
        "Player {} {:+d} 💠 ({}, game {}) -> balance {}",
        player.telegram_user_id,
        signed_amount,
        entry.reason,
        entry.game_id,
        player.pixels,
    )
    return tx


def credit(
    session: Session,
    player: Player,
    amount: int,
    entry: LedgerEntry,
) -> PixelTransaction:
    if amount <= 0:
        msg = f"credit amount must be positive, got {amount}"
        raise ValueError(msg)
    return _record(session, player, amount, entry)


def debit(
    session: Session,
    player: Player,
    amount: int,
    entry: LedgerEntry,
) -> PixelTransaction:
    if amount <= 0:
        msg = f"debit amount must be positive, got {amount}"
        raise ValueError(msg)
    if player.pixels < amount:
        logger.warning(
            "Player {} can't afford {} 💠 ({}): balance {}",
            player.telegram_user_id,
            amount,
            entry.reason,
            player.pixels,
        )
        raise InsufficientPixelsError(player.pixels, amount)
    return _record(session, player, -amount, entry)


def balance(session: Session, player_id: int) -> int:
    player = session.get(Player, player_id)
    return 0 if player is None else player.pixels


def game_total(session: Session, *, player_id: int, game_id: int, reason: PixelReason) -> int:
    stmt = select(func.coalesce(func.sum(PixelTransaction.amount), 0)).where(
        PixelTransaction.player_id == player_id,
        PixelTransaction.game_id == game_id,
        PixelTransaction.reason == reason,
    )
    return int(session.scalar(stmt) or 0)


def game_has(session: Session, *, game_id: int, reason: PixelReason) -> bool:
    stmt = (
        select(PixelTransaction.id)
        .where(PixelTransaction.game_id == game_id, PixelTransaction.reason == reason)
        .limit(1)
    )
    return session.scalar(stmt) is not None
