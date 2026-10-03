"""add pixel economy: balance, ledger, config, turn_received_at

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
    op.add_column("players", sa.Column("pixels", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("turn_state", sa.Column("turn_received_at", sa.DateTime(), nullable=True))
    op.add_column("games", sa.Column("turn_received_at", sa.DateTime(), nullable=True))
    op.create_table(
        "pixel_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("detail", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.telegram_user_id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pixel_transactions_player_id"), "pixel_transactions", ["player_id"])
    op.create_index(op.f("ix_pixel_transactions_game_id"), "pixel_transactions", ["game_id"])
    op.create_table(
        "pixel_config",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("pixel_config")
    # No explicit drop_index for ix_pixel_transactions_*: on MariaDB/InnoDB
    # those indexes back the foreign keys, so dropping them first fails
    # (error 1553); drop_table removes them along with the table.
    op.drop_table("pixel_transactions")
    op.drop_column("games", "turn_received_at")
    op.drop_column("turn_state", "turn_received_at")
    op.drop_column("players", "pixels")
