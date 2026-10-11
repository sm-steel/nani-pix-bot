"""add season objectives

Revision ID: d9f1b3c5e7a2
Revises: c8e0a2b4d6f1
Create Date: 2026-10-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9f1b3c5e7a2"
down_revision: str | Sequence[str] | None = "c8e0a2b4d6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "season_objectives",
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("objective_key", sa.String(length=32), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["season_schedule.id"],
            name=op.f("fk_season_objectives_season_id_season_schedule"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_season_objectives_player_id_players"),
        ),
        sa.PrimaryKeyConstraint(
            "season_id", "player_id", "objective_key", name=op.f("pk_season_objectives")
        ),
    )


def downgrade() -> None:
    op.drop_table("season_objectives")
