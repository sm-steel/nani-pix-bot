"""add clue purchases and in-play screenshot urls

Revision ID: c7e4a1d9f2b6
Revises: b5d8f2a6c9e3
Create Date: 2026-10-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e4a1d9f2b6"
down_revision: str | Sequence[str] | None = "b5d8f2a6c9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("shown_screenshot_urls", sa.JSON(), nullable=True))
    op.create_table(
        "clue_purchases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("tile_index", sa.Integer(), nullable=True),
        sa.Column("screenshot_url", sa.String(1024), nullable=True),
        sa.Column("telegram_file_id", sa.String(256), nullable=True),
        sa.Column("shared_at", sa.DateTime(), nullable=True),
        sa.Column("transfer_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.telegram_user_id"]),
        sa.ForeignKeyConstraint(["transfer_id"], ["currency_transfers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transfer_id"),
    )
    op.create_index(op.f("ix_clue_purchases_game_id"), "clue_purchases", ["game_id"])
    op.create_index(op.f("ix_clue_purchases_player_id"), "clue_purchases", ["player_id"])


def downgrade() -> None:
    # No explicit drop_index for ix_clue_purchases_*: on MariaDB/InnoDB those
    # indexes back the foreign keys, so dropping them first fails (error 1553);
    # drop_table removes them along with the table.
    op.drop_table("clue_purchases")
    op.drop_column("games", "shown_screenshot_urls")
