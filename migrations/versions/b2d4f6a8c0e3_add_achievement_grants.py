"""add achievement grants and claims

Revision ID: b2d4f6a8c0e3
Revises: a1c3e5f7b9d2
Create Date: 2026-10-07 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2d4f6a8c0e3"
down_revision: str | Sequence[str] | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "achievement_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "player_id", sa.BigInteger(), sa.ForeignKey("players.telegram_user_id"), nullable=False
        ),
        sa.Column("key", sa.String(48), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("period_key", sa.String(16), nullable=False, server_default=""),
        sa.Column("rarity", sa.String(16), nullable=False),
        sa.Column("reward", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column(
            "transfer_id", sa.Integer(), sa.ForeignKey("currency_transfers.id"), nullable=True
        ),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("player_id", "key", "tier", "period_key", name="uq_achievement_grant"),
    )
    op.create_index("ix_achievement_grants_player_id", "achievement_grants", ["player_id"])
    op.create_table(
        "achievement_claims",
        sa.Column("key", sa.String(48), primary_key=True),
        sa.Column("tier", sa.Integer(), primary_key=True),
        sa.Column(
            "player_id", sa.BigInteger(), sa.ForeignKey("players.telegram_user_id"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("achievement_claims")
    op.drop_table("achievement_grants")
