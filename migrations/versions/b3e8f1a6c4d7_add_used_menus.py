"""used_menus: a menu message whose one action already ran (issue #479)

Revision ID: b3e8f1a6c4d7
Revises: e7a1c3f5b9d2
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e8f1a6c4d7"
down_revision: str | Sequence[str] | None = "e7a1c3f5b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "used_menus",
        sa.Column("chat_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("message_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.PrimaryKeyConstraint("chat_id", "message_id"),
    )


def downgrade() -> None:
    op.drop_table("used_menus")
