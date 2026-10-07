"""add period results

Revision ID: d4f6b8c0e2a5
Revises: c3e5a7b9d1f4
Create Date: 2026-10-07 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6b8c0e2a5"
down_revision: str | Sequence[str] | None = "c3e5a7b9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "period_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("period_type", sa.String(8), nullable=False),
        sa.Column("period_key", sa.String(16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "player_id", sa.BigInteger(), sa.ForeignKey("players.telegram_user_id"), nullable=False
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.UniqueConstraint("period_type", "period_key", "rank", name="uq_period_result_rank"),
    )
    op.create_table(
        "period_state",
        sa.Column("period_type", sa.String(8), primary_key=True),
        sa.Column("next_end", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("period_state")
    op.drop_table("period_results")
