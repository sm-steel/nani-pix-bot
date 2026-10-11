"""add consumables and token-paid clue purchases

Revision ID: a4d6c8e2f1b5
Revises: f1c3a5e7b9d2
Create Date: 2026-10-12 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4d6c8e2f1b5"
down_revision: str | Sequence[str] | None = "f1c3a5e7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAID_ONCE = (
    "(token_used = 1 AND transfer_id IS NULL) OR (token_used = 0 AND transfer_id IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        "consumables",
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.CheckConstraint("count >= 0", name=op.f("ck_consumables_count_non_negative")),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_consumables_player_id_players"),
        ),
        sa.PrimaryKeyConstraint("player_id", "kind", name=op.f("pk_consumables")),
    )
    with op.batch_alter_table("clue_purchases") as batch:
        batch.alter_column("transfer_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(
            sa.Column("token_used", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.create_check_constraint(op.f("ck_clue_purchases_paid_once"), PAID_ONCE)


def downgrade() -> None:
    # Not data-safe for token purchases: they have no charge to point at, so
    # their rows are deleted before transfer_id becomes NOT NULL again.
    op.execute("DELETE FROM clue_purchases WHERE token_used = 1")
    with op.batch_alter_table("clue_purchases") as batch:
        batch.drop_constraint(op.f("ck_clue_purchases_paid_once"), type_="check")
        batch.drop_column("token_used")
        batch.alter_column("transfer_id", existing_type=sa.Integer(), nullable=False)
    op.drop_table("consumables")
