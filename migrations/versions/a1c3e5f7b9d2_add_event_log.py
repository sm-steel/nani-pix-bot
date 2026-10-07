"""add event log and games.activated_at

Revision ID: a1c3e5f7b9d2
Revises: e6a2b8c4d9f1
Create Date: 2026-10-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c3e5f7b9d2"
down_revision: str | Sequence[str] | None = "e6a2b8c4d9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_id", sa.BigInteger(), nullable=True),
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_event_log_occurred_at", "event_log", ["occurred_at"])
    op.create_index("ix_event_log_game_id", "event_log", ["game_id"])
    op.create_index("ix_event_log_type_actor", "event_log", ["event_type", "actor_id"])
    op.create_index("ix_event_log_type_subject", "event_log", ["event_type", "subject_id"])
    op.add_column("games", sa.Column("activated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "activated_at")
    op.drop_table("event_log")
