"""add currency economy: balance, transfer ledger, config, turn_received_at

Revision ID: b5d8f2a6c9e3
Revises: e3a91c7b5d24
Create Date: 2026-10-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5d8f2a6c9e3"
down_revision: str | Sequence[str] | None = "e3a91c7b5d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default required: players already has live rows, and a NOT
    # NULL column added to a non-empty table needs a backfill value on
    # MariaDB (same hazard d94e2b71a608 documents).
    op.add_column(
        "players", sa.Column("currency", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("turn_state", sa.Column("turn_received_at", sa.DateTime(), nullable=True))
    op.add_column("games", sa.Column("turn_received_at", sa.DateTime(), nullable=True))
    op.create_table(
        "currency_transfers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_type", sa.String(16), nullable=False),
        sa.Column("from_player_id", sa.BigInteger(), nullable=True),
        sa.Column("to_type", sa.String(16), nullable=False),
        sa.Column("to_player_id", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        # No FK on game_id: the ledger outlives deleted games.
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.Column("reverses_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "from_type IN ('house', 'player', 'pot')",
            name="ck_currency_transfers_from_type_valid",
        ),
        sa.CheckConstraint(
            "to_type IN ('house', 'player', 'pot')",
            name="ck_currency_transfers_to_type_valid",
        ),
        sa.CheckConstraint("amount > 0", name="ck_currency_transfers_amount_positive"),
        sa.CheckConstraint(
            "(from_type = 'player' AND from_player_id IS NOT NULL)"
            " OR (from_type <> 'player' AND from_player_id IS NULL)",
            name="ck_currency_transfers_from_player",
        ),
        sa.CheckConstraint(
            "(to_type = 'player' AND to_player_id IS NOT NULL)"
            " OR (to_type <> 'player' AND to_player_id IS NULL)",
            name="ck_currency_transfers_to_player",
        ),
        sa.CheckConstraint(
            "(from_type <> 'pot' AND to_type <> 'pot') OR game_id IS NOT NULL",
            name="ck_currency_transfers_pot_has_game",
        ),
        sa.CheckConstraint(
            "from_type <> to_type OR (from_type = 'player' AND from_player_id <> to_player_id)",
            name="ck_currency_transfers_distinct_sides",
        ),
        sa.ForeignKeyConstraint(["from_player_id"], ["players.telegram_user_id"]),
        sa.ForeignKeyConstraint(["to_player_id"], ["players.telegram_user_id"]),
        sa.ForeignKeyConstraint(["reverses_id"], ["currency_transfers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reverses_id"),
    )
    op.create_index(
        op.f("ix_currency_transfers_from_player_id"), "currency_transfers", ["from_player_id"]
    )
    op.create_index(
        op.f("ix_currency_transfers_to_player_id"), "currency_transfers", ["to_player_id"]
    )
    op.create_index(op.f("ix_currency_transfers_game_id"), "currency_transfers", ["game_id"])
    op.create_table(
        "currency_config",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("currency_config")
    # No explicit drop_index for ix_currency_transfers_*: on MariaDB/InnoDB
    # those indexes back the foreign keys, so dropping them first fails
    # (error 1553); drop_table removes them along with the table.
    op.drop_table("currency_transfers")
    op.drop_column("games", "turn_received_at")
    op.drop_column("turn_state", "turn_received_at")
    op.drop_column("players", "currency")
