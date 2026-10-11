"""The only code that moves 💠 and 🪙. Every movement adds one two-sided
`CurrencyTransfer` row *and* updates the cached balance of any player side
(`Player.currency` for 💠, `season_balances` for 🪙), in the caller's session,
so they commit (or roll back) together."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import Currency, CurrencyParty, CurrencyReason, EventType
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonBalance
from nani_pix_bot.services import events

# Money coming back rather than being earned: refunds (always naming the
# charge via reverses_id) and HARD MODE cashback. Achievements ignore
# these entirely (spec §2).
REVERSAL_REASONS: frozenset[CurrencyReason] = frozenset(
    {CurrencyReason.REFUND, CurrencyReason.CASHBACK}
)


@dataclass(frozen=True)
class LedgerEntry:
    """What a transfer is for — the ledger row's non-amount, non-party fields.
    `coins` moves `season_id`'s 🪙 instead of 💠; a 💠 row may still name the
    season it belongs to (Premium, conversion, pass rewards)."""

    reason: CurrencyReason
    game_id: int | None = None
    reverses_id: int | None = None
    season_id: int | None = None
    coins: bool = False

    def __post_init__(self) -> None:
        if self.coins and self.season_id is None:
            msg = "season coins need a season_id"
            raise ValueError(msg)

    @property
    def currency(self) -> Currency:
        return Currency.SEASON if self.coins else Currency.BASE

    @property
    def unit(self) -> str:
        return "🪙" if self.coins else "💠"

    @property
    def coin_season_id(self) -> int | None:
        """The season whose 🪙 this moves; None when it moves 💠."""
        return self.season_id if self.coins else None

    def log_fields(self) -> dict[str, object]:
        """`reason` and `currency`, plus `game_id`/`season_id` when set: without
        one the log context's value stands, rather than being overridden with None."""
        fields: dict[str, object] = {"reason": self.reason.value, "currency": self.currency.value}
        if self.game_id is not None:
            fields["game_id"] = self.game_id
        if self.season_id is not None:
            fields["season_id"] = self.season_id
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


def _season_balance(session: Session, season_id: int, player_id: int) -> SeasonBalance:
    row = session.get(SeasonBalance, (season_id, player_id))
    if row is None:
        row = SeasonBalance(season_id=season_id, player_id=player_id, coins=0)
        session.add(row)
    return row


def _held(session: Session, player: Player, entry: LedgerEntry) -> int:
    season_id = entry.coin_season_id
    if season_id is None:
        return player.currency
    return _season_balance(session, season_id, player.telegram_user_id).coins


def _adjust(session: Session, player: Player, entry: LedgerEntry, delta: int) -> None:
    season_id = entry.coin_season_id
    if season_id is None:
        player.currency += delta
    else:
        _season_balance(session, season_id, player.telegram_user_id).coins += delta


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
    held = 0 if source.player is None else _held(session, source.player, entry)
    if source.player is not None and held < amount:
        # Every player-side source is the paying player's own command (a
        # clue, /sharpen, /bounty, /tip), so the log context names them.
        logger.warning(
            "can't afford {amount} {unit} ({reason}): balance {balance}",
            amount=amount,
            unit=entry.unit,
            balance=held,
            payer_id=source.player.telegram_user_id,
            **entry.log_fields(),
        )
        raise InsufficientCurrencyError(held, amount)
    if source.player is not None:
        _adjust(session, source.player, entry, -amount)
    if target.player is not None:
        _adjust(session, target.player, entry, amount)
    row = CurrencyTransfer(
        from_type=source.type,
        from_player_id=source.player_id,
        to_type=target.type,
        to_player_id=target.player_id,
        amount=amount,
        reason=entry.reason,
        game_id=entry.game_id,
        reverses_id=entry.reverses_id,
        currency=entry.currency,
        season_id=entry.season_id,
    )
    session.add(row)
    logger.debug(
        "transfer {amount} {unit}: {from_type} {from_id} -> {to_type} {to_id} ({reason})",
        amount=amount,
        unit=entry.unit,
        from_type=source.type.value,
        from_id=source.player_id,
        to_type=target.type.value,
        to_id=target.player_id,
        **entry.log_fields(),
    )
    events.emit(
        session,
        EventType.CURRENCY_MOVED,
        events.Involved(
            actor_id=source.player_id, subject_id=target.player_id, game_id=entry.game_id
        ),
        amount=amount,
        reason=entry.reason.value,
        currency=entry.currency.value,
        season_id=entry.season_id,
        from_type=source.type.value,
        to_type=target.type.value,
        reversal=entry.reverses_id is not None or entry.reason in REVERSAL_REASONS,
    )
    return row


def credit(session: Session, player: Player, amount: int, entry: LedgerEntry) -> CurrencyTransfer:
    return transfer(session, Party.house(), Party.of(player), amount, entry)


def debit(session: Session, player: Player, amount: int, entry: LedgerEntry) -> CurrencyTransfer:
    return transfer(session, Party.of(player), Party.house(), amount, entry)


def balance(session: Session, player_id: int, season_id: int | None = None) -> int:
    """💠, or with `season_id` that season's 🪙."""
    if season_id is not None:
        row = session.get(SeasonBalance, (season_id, player_id))
        return 0 if row is None else row.coins
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
