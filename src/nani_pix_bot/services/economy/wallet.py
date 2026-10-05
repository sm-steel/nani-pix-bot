"""The only code that moves 💠. Every movement adds one two-sided
`CurrencyTransfer` row *and* updates the cached `Player.currency` of any player
side, in the caller's session, so they commit (or roll back) together."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import CurrencyParty, CurrencyReason
from nani_pix_bot.models.player import Player


@dataclass(frozen=True)
class LedgerEntry:
    """What a transfer is for — the ledger row's non-amount, non-party fields."""

    reason: CurrencyReason
    game_id: int | None = None
    reverses_id: int | None = None

    def log_fields(self) -> dict[str, object]:
        """`reason`, plus `game_id` when set: without one the log context's
        game stands, rather than being overridden with None."""
        fields: dict[str, object] = {"reason": self.reason.value}
        if self.game_id is not None:
            fields["game_id"] = self.game_id
        return fields


@dataclass(frozen=True)
class Party:
    """One side of a transfer; `player` is set exactly for a player side."""

    type: CurrencyParty
    player: Player | None = None

    def __post_init__(self) -> None:
        if (self.type is CurrencyParty.PLAYER) != (self.player is not None):
            msg = f"player must be set exactly when type is player, got {self.type}"
            raise ValueError(msg)

    @classmethod
    def house(cls) -> "Party":
        return cls(CurrencyParty.HOUSE)

    @classmethod
    def of(cls, player: Player) -> "Party":
        return cls(CurrencyParty.PLAYER, player)

    @classmethod
    def pot(cls) -> "Party":
        return cls(CurrencyParty.POT)

    @property
    def player_id(self) -> int | None:
        return None if self.player is None else self.player.telegram_user_id


class InsufficientCurrencyError(Exception):
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
) -> CurrencyTransfer:
    if amount <= 0:
        msg = f"transfer amount must be positive, got {amount}"
        raise ValueError(msg)
    if source == target:
        msg = f"can't transfer between the same party ({source.type}, {source.player_id})"
        raise ValueError(msg)
    if source.player is not None and source.player.currency < amount:
        # Every player-side source is the paying player's own command (a
        # clue, /sharpen, /bounty, /tip), so the log context names them.
        logger.warning(
            "can't afford {amount} 💠 ({reason}): balance {balance}",
            amount=amount,
            balance=source.player.currency,
            payer_id=source.player.telegram_user_id,
            **entry.log_fields(),
        )
        raise InsufficientCurrencyError(source.player.currency, amount)
    if source.player is not None:
        source.player.currency -= amount
    if target.player is not None:
        target.player.currency += amount
    row = CurrencyTransfer(
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
        "transfer {amount} 💠: {from_type} {from_id} -> {to_type} {to_id} ({reason})",
        amount=amount,
        from_type=source.type.value,
        from_id=source.player_id,
        to_type=target.type.value,
        to_id=target.player_id,
        **entry.log_fields(),
    )
    return row


def credit(session: Session, player: Player, amount: int, entry: LedgerEntry) -> CurrencyTransfer:
    return transfer(session, Party.house(), Party.of(player), amount, entry)


def debit(session: Session, player: Player, amount: int, entry: LedgerEntry) -> CurrencyTransfer:
    return transfer(session, Party.of(player), Party.house(), amount, entry)


def balance(session: Session, player_id: int) -> int:
    player = session.get(Player, player_id)
    return 0 if player is None else player.currency


def game_total(session: Session, *, player_id: int, game_id: int, reason: CurrencyReason) -> int:
    stmt = select(func.coalesce(func.sum(CurrencyTransfer.amount), 0)).where(
        CurrencyTransfer.to_type == CurrencyParty.PLAYER,
        CurrencyTransfer.to_player_id == player_id,
        CurrencyTransfer.game_id == game_id,
        CurrencyTransfer.reason == reason,
    )
    return int(session.scalar(stmt) or 0)


def ledger_balance(session: Session, player_id: int) -> int:
    """Audit counterpart of the cached `players.currency`: transfers in minus out."""
    received = select(func.coalesce(func.sum(CurrencyTransfer.amount), 0)).where(
        CurrencyTransfer.to_type == CurrencyParty.PLAYER, CurrencyTransfer.to_player_id == player_id
    )
    sent = select(func.coalesce(func.sum(CurrencyTransfer.amount), 0)).where(
        CurrencyTransfer.from_type == CurrencyParty.PLAYER,
        CurrencyTransfer.from_player_id == player_id,
    )
    return int(session.scalar(received) or 0) - int(session.scalar(sent) or 0)
