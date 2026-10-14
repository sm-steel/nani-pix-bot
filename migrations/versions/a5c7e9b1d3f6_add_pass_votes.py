"""add pass_votes

Revision ID: a5c7e9b1d3f6
Revises: d7a3f1c9b5e2
Create Date: 2026-10-14 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5c7e9b1d3f6"
down_revision: str | Sequence[str] | None = "d7a3f1c9b5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pass_votes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.telegram_user_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id", "player_id", "stage", name="uq_pass_votes_game_player_stage"
        ),
    )
    op.create_index(op.f("ix_pass_votes_game_id"), "pass_votes", ["game_id"])


def downgrade() -> None:
    """Downgrade schema. Just drop_table: dropping the game_id index first fails on MariaDB
    (error 1553, the foreign key still needs it; see c8e0a2b4d6f1)."""
    op.drop_table("pass_votes")
