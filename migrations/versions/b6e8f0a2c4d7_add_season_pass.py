"""add season pass progress

Revision ID: b6e8f0a2c4d7
Revises: a4d6c8e2f1b5
Create Date: 2026-10-12 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6e8f0a2c4d7"
down_revision: str | Sequence[str] | None = "a4d6c8e2f1b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "season_pass",
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("free_claimed", sa.Integer(), nullable=False),
        sa.Column("premium_claimed", sa.Integer(), nullable=False),
        sa.Column("premium_transfer_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["season_schedule.id"],
            name=op.f("fk_season_pass_season_id_season_schedule"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_season_pass_player_id_players"),
        ),
        sa.ForeignKeyConstraint(
            ["premium_transfer_id"],
            ["currency_transfers.id"],
            name=op.f("fk_season_pass_premium_transfer_id_currency_transfers"),
        ),
        sa.PrimaryKeyConstraint("season_id", "player_id", name=op.f("pk_season_pass")),
        sa.UniqueConstraint("premium_transfer_id", name=op.f("uq_season_pass_premium_transfer_id")),
    )


def downgrade() -> None:
    op.drop_table("season_pass")
