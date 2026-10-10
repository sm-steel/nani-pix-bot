"""add season_schedule

Revision ID: d1a5e7c3b9f2
Revises: c3f8a1d6e2b9
Create Date: 2026-10-10 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1a5e7c3b9f2"
down_revision: str | Sequence[str] | None = "c3f8a1d6e2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema. Starts empty: seasons are off until an admin schedules one."""
    op.create_table(
        "season_schedule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(48), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_season_schedule")),
    )
    op.create_index(op.f("ix_season_schedule_run_id"), "season_schedule", ["run_id"])
    op.create_index(op.f("ix_season_schedule_status"), "season_schedule", ["status"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("season_schedule")
