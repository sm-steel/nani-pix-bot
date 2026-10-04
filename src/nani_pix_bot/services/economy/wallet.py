"""The only code that moves 💠. Every movement adds one two-sided
`PixelTransfer` row *and* updates the cached `Player.pixels` of any player
side, in the caller's session, so they commit (or roll back) together."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import PixelParty, PixelReason
from nani_pix_bot.models.pixel_transfer import PixelTransfer
from nani_pix_bot.models.player import Player


@dataclass(frozen=True)
class LedgerEntry:
    """What a transfer is for — the ledger row's non-amount, non-party fields."""

    reason: PixelReason
    game_id: int | None = None
    reverses_id: int | None = None


@dataclass(frozen=True)
class Party:
    """One side of a transfer; `player` is set exactly for a player side."""

    type: PixelParty
    player: Player | None = None

    def __post_init__(self) -> None:
        if (self.type is PixelParty.PLAYER) != (self.player is not None):
            msg = f"player must be set exactly when type is player, got {self.type}"
            raise ValueError(msg)

    @classmethod
    def house(cls) -> "Party":
        return cls(PixelParty.HOUSE)

    @classmethod
    def of(cls, player: Player) -> "Party":
        return cls(PixelParty.PLAYER, player)

    @classmethod
    def pot(cls) -> "Party":
        return cls(PixelParty.POT)

    @property
    def player_id(self) -> int | None:
        return None if self.player is None else self.player.telegram_user_id


class InsufficientPixelsError(Exception):
    def __init__(self, balance: int, amount: int) -> None:
        super().__init__(f"balance {balance} < {amount}")
        self.balance = balance
        self.amount = amount


def transfer(
    session: Session,
    source: Party,
    target: Party,
    amount: int,
    entry: LedgerEntry,
) -> PixelTransfer:
    if amount <= 0:
        msg = f"transfer amount must be positive, got {amount}"
        raise ValueError(msg)
    if source == target:
        msg = f"can't transfer between the same party ({source.type}, {source.player_id})"
        raise ValueError(msg)
    if source.player is not None and source.player.pixels < amount:
        logger.warning(
            "Player {} can't afford {} 💠 ({}): balance {}",
            source.player_id,
            amount,
            entry.reason,
            source.player.pixels,
        )
        raise InsufficientPixelsError(source.player.pixels, amount)
    if source.player is not None:
        source.player.pixels -= amount
    if target.player is not None:
        target.player.pixels += amount
    row = PixelTransfer(
        from_type=source.type,
        from_player_id=source.player_id,
        to_type=target.type,
        to_player_id=target.player_id,
        amount=amount,
        reason=entry.reason,
        game_id=entry.game_id,
        reverses_id=entry.reverses_id,
    )
    session.add(row)
    logger.debug(
        "Transfer {} 💠: {} {} -> {} {} ({}, game {})",
        amount,
        source.type,
        source.player_id,
        target.type,
        target.player_id,
        entry.reason,
        entry.game_id,
    )
    return row


def credit(session: Session, player: Player, amount: int, entry: LedgerEntry) -> PixelTransfer:
    return transfer(session, Party.house(), Party.of(player), amount, entry)


def debit(session: Session, player: Player, amount: int, entry: LedgerEntry) -> PixelTransfer:
    return transfer(session, Party.of(player), Party.house(), amount, entry)


def balance(session: Session, player_id: int) -> int:
    player = session.get(Player, player_id)
    return 0 if player is None else player.pixels


def game_total(session: Session, *, player_id: int, game_id: int, reason: PixelReason) -> int:
    stmt = select(func.coalesce(func.sum(PixelTransfer.amount), 0)).where(
        PixelTransfer.to_type == PixelParty.PLAYER,
        PixelTransfer.to_player_id == player_id,
        PixelTransfer.game_id == game_id,
        PixelTransfer.reason == reason,
    )
    return int(session.scalar(stmt) or 0)


def ledger_balance(session: Session, player_id: int) -> int:
    """Audit counterpart of the cached `players.pixels`: transfers in minus out."""
    received = select(func.coalesce(func.sum(PixelTransfer.amount), 0)).where(
        PixelTransfer.to_type == PixelParty.PLAYER, PixelTransfer.to_player_id == player_id
    )
    sent = select(func.coalesce(func.sum(PixelTransfer.amount), 0)).where(
        PixelTransfer.from_type == PixelParty.PLAYER, PixelTransfer.from_player_id == player_id
    )
    return int(session.scalar(received) or 0) - int(session.scalar(sent) or 0)
