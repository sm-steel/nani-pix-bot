"""add the season currency to the ledger

Revision ID: f1c3a5e7b9d2
Revises: e6b1d4f8a2c3
Create Date: 2026-10-11 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1c3a5e7b9d2"
down_revision: str | Sequence[str] | None = "e6b1d4f8a2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("currency_transfers") as batch:
        batch.add_column(
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="base")
        )
        batch.add_column(sa.Column("season_id", sa.Integer(), nullable=True))
        batch.create_index(op.f("ix_currency_transfers_season_id"), ["season_id"])
        batch.create_foreign_key(
            op.f("fk_currency_transfers_season_id_season_schedule"),
            "season_schedule",
            ["season_id"],
            ["id"],
        )
        batch.create_check_constraint(
            op.f("ck_currency_transfers_currency_valid"), "currency IN ('base', 'season')"
        )
        batch.create_check_constraint(
            op.f("ck_currency_transfers_season_coins_have_season"),
            "currency <> 'season' OR season_id IS NOT NULL",
        )
    op.create_table(
        "season_balances",
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("coins", sa.Integer(), nullable=False),
        sa.CheckConstraint("coins >= 0", name=op.f("ck_season_balances_coins_non_negative")),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["season_schedule.id"],
            name=op.f("fk_season_balances_season_id_season_schedule"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_season_balances_player_id_players"),
        ),
        sa.PrimaryKeyConstraint("season_id", "player_id", name=op.f("pk_season_balances")),
    )


def downgrade() -> None:
    # Not data-safe for season coins: without the currency column their rows
    # would read as 💠, so they are deleted first.
    op.execute("DELETE FROM currency_transfers WHERE currency = 'season'")
    op.drop_table("season_balances")
    with op.batch_alter_table("currency_transfers") as batch:
        batch.drop_constraint(op.f("ck_currency_transfers_season_coins_have_season"), type_="check")
        batch.drop_constraint(op.f("ck_currency_transfers_currency_valid"), type_="check")
        batch.drop_constraint(
            op.f("fk_currency_transfers_season_id_season_schedule"), type_="foreignkey"
        )
        batch.drop_index(op.f("ix_currency_transfers_season_id"))
        batch.drop_column("season_id")
        batch.drop_column("currency")
