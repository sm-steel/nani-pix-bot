from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import Currency, CurrencyParty, CurrencyReason

# Longest CurrencyReason value plus headroom for later phases' reasons.
REASON_LENGTH = 32
# Longest CurrencyParty value plus headroom.
PARTY_LENGTH = 16
CURRENCY_LENGTH = 8


class CurrencyTransfer(Base):
    """One currency movement, both sides explicit (house / player / pot) — the
    append-only ledger behind `Player.currency`, which is only a cache of it and
    moves in the same transaction. Written only via services/economy/wallet.py.
    The per-game wrong-guess cap is answered by summing these rows, so nothing
    about earnings is held in memory.

    `game_id` is deliberately a plain integer, not a foreign key: /stop and
    setup-abandon delete Game rows, the ledger must outlive them, and a pot
    row must keep naming its game. `reverses_id` names the row a refund undoes;
    its unique index blocks a double refund.

    The party types are CHECK-restricted to CurrencyParty's values, but `reason`
    deliberately is not: CurrencyReason is an open set that grows every phase, so
    a CHECK on it would need a migration each time (hence a plain String)."""

    __tablename__ = "currency_transfers"
    __table_args__ = (
        CheckConstraint(
            "from_type IN ('house', 'player', 'pot')",
            name="ck_currency_transfers_from_type_valid",
        ),
        CheckConstraint(
            "to_type IN ('house', 'player', 'pot')",
            name="ck_currency_transfers_to_type_valid",
        ),
        CheckConstraint("amount > 0", name="ck_currency_transfers_amount_positive"),
        CheckConstraint(
            "(from_type = 'player' AND from_player_id IS NOT NULL)"
            " OR (from_type <> 'player' AND from_player_id IS NULL)",
            name="ck_currency_transfers_from_player",
        ),
        CheckConstraint(
            "(to_type = 'player' AND to_player_id IS NOT NULL)"
            " OR (to_type <> 'player' AND to_player_id IS NULL)",
            name="ck_currency_transfers_to_player",
        ),
        CheckConstraint(
            "(from_type <> 'pot' AND to_type <> 'pot') OR game_id IS NOT NULL",
            name="ck_currency_transfers_pot_has_game",
        ),
        CheckConstraint(
            "from_type <> to_type OR (from_type = 'player' AND from_player_id <> to_player_id)",
            name="ck_currency_transfers_distinct_sides",
        ),
        CheckConstraint(
            "currency IN ('base', 'season')", name="ck_currency_transfers_currency_valid"
        ),
        CheckConstraint(
            "currency <> 'season' OR season_id IS NOT NULL",
            name="ck_currency_transfers_season_coins_have_season",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_type: Mapped[CurrencyParty] = mapped_column(String(PARTY_LENGTH))
    from_player_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), default=None, index=True
    )
    to_type: Mapped[CurrencyParty] = mapped_column(String(PARTY_LENGTH))
    to_player_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), default=None, index=True
    )
    amount: Mapped[int]
    reason: Mapped[CurrencyReason] = mapped_column(String(REASON_LENGTH))
    game_id: Mapped[int | None] = mapped_column(default=None, index=True)
    reverses_id: Mapped[int | None] = mapped_column(
        ForeignKey("currency_transfers.id"), default=None, unique=True
    )
    currency: Mapped[Currency] = mapped_column(
        String(CURRENCY_LENGTH), default=Currency.BASE, server_default=Currency.BASE.value
    )
    # Which season the row belongs to: required for 🪙, optional for 💠 (a
    # Premium purchase, a converted leftover, a pass reward paid in 💠).
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("season_schedule.id"), index=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
