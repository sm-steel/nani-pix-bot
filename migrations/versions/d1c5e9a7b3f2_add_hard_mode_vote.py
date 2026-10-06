"""add hard-mode vote

Revision ID: d1c5e9a7b3f2
Revises: b8e2d4f6a1c3
Create Date: 2026-10-06 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1c5e9a7b3f2"
down_revision: str | Sequence[str] | None = "b8e2d4f6a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Widen games.status's native ENUM with VOTING (MariaDB needs the whole
    list restated — see c2a7f0d94b31), then add the vote columns and table."""
    op.alter_column(
        "games",
        "status",
        existing_type=sa.Enum("SETUP", "ACTIVE", "WON", "UNSOLVED", name="gamestatus"),
        type_=sa.Enum("SETUP", "ACTIVE", "WON", "UNSOLVED", "VOTING", name="gamestatus"),
        existing_nullable=False,
    )
    op.add_column("games", sa.Column("vote_deadline_at", sa.DateTime(), nullable=True))
    op.add_column("games", sa.Column("vote_message_id", sa.Integer(), nullable=True))
    op.create_table(
        "game_votes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("voter_id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voter_id"], ["players.telegram_user_id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["players.telegram_user_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "voter_id", name="uq_game_votes_game_voter"),
    )
    op.create_index(op.f("ix_game_votes_game_id"), "game_votes", ["game_id"])


def downgrade() -> None:
    """Not data-safe if any game is VOTING (the narrower ENUM rejects it)."""
    op.drop_table("game_votes")
    op.drop_column("games", "vote_message_id")
    op.drop_column("games", "vote_deadline_at")
    op.alter_column(
        "games",
        "status",
        existing_type=sa.Enum("SETUP", "ACTIVE", "WON", "UNSOLVED", "VOTING", name="gamestatus"),
        type_=sa.Enum("SETUP", "ACTIVE", "WON", "UNSOLVED", name="gamestatus"),
        existing_nullable=False,
    )
