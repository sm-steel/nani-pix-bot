"""add game guesses

Revision ID: a3f9c2d7e1b4
Revises: c7e4a1d9f2b6
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f9c2d7e1b4"
down_revision: str | Sequence[str] | None = "c7e4a1d9f2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_guesses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.String(255), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("partial_reveal", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.telegram_user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_game_guesses_game_id"), "game_guesses", ["game_id"])
    op.create_index(op.f("ix_game_guesses_player_id"), "game_guesses", ["player_id"])


def downgrade() -> None:
    # The indexes back the FKs on MariaDB (error 1553 if dropped first) — see c7e4a1d9f2b6.
    op.drop_table("game_guesses")
