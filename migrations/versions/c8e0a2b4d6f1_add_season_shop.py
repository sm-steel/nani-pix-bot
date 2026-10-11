"""add season shop purchases

Revision ID: c8e0a2b4d6f1
Revises: b6e8f0a2c4d7
Create Date: 2026-10-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e0a2b4d6f1"
down_revision: str | Sequence[str] | None = "b6e8f0a2c4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "season_shop_purchases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("offer_key", sa.String(length=96), nullable=False),
        sa.Column("transfer_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["season_schedule.id"],
            name=op.f("fk_season_shop_purchases_season_id_season_schedule"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_season_shop_purchases_player_id_players"),
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["currency_transfers.id"],
            name=op.f("fk_season_shop_purchases_transfer_id_currency_transfers"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_season_shop_purchases")),
        sa.UniqueConstraint("transfer_id", name=op.f("uq_season_shop_purchases_transfer_id")),
    )
    op.create_index(
        op.f("ix_season_shop_purchases_season_id"), "season_shop_purchases", ["season_id"]
    )
    op.create_index(
        op.f("ix_season_shop_purchases_player_id"), "season_shop_purchases", ["player_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_season_shop_purchases_player_id"), table_name="season_shop_purchases")
    op.drop_index(op.f("ix_season_shop_purchases_season_id"), table_name="season_shop_purchases")
    op.drop_table("season_shop_purchases")
