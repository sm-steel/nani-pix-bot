"""add season_results

Revision ID: f7c1d9e3a5b8
Revises: e4b8c2f6a1d7
Create Date: 2026-10-10 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7c1d9e3a5b8"
down_revision: str | Sequence[str] | None = "e4b8c2f6a1d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "season_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_season_results")),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["season_schedule.id"],
            name=op.f("fk_season_results_season_id_season_schedule"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_season_results_player_id_players"),
        ),
        sa.UniqueConstraint("season_id", "player_id", name="uq_season_result_player"),
    )
    op.create_index(op.f("ix_season_results_season_id"), "season_results", ["season_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("season_results")
