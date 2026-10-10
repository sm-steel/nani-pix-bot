"""add owned_items and equipped_items

Revision ID: b3e7a9c5d2f4
Revises: a2d6f8b4c1e9
Create Date: 2026-10-10 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e7a9c5d2f4"
down_revision: str | Sequence[str] | None = "a2d6f8b4c1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "owned_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("item_id", sa.String(length=96), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=True),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_owned_items")),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_owned_items_player_id_players"),
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["season_schedule.id"],
            name=op.f("fk_owned_items_season_id_season_schedule"),
        ),
        sa.UniqueConstraint("player_id", "item_id", name="uq_owned_item"),
    )
    op.create_index(op.f("ix_owned_items_player_id"), "owned_items", ["player_id"])
    op.create_table(
        "equipped_items",
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("slot", sa.String(length=24), nullable=False),
        sa.Column("item_id", sa.String(length=96), nullable=False),
        sa.PrimaryKeyConstraint("player_id", "slot", name=op.f("pk_equipped_items")),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_equipped_items_player_id_players"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("equipped_items")
    op.drop_table("owned_items")
