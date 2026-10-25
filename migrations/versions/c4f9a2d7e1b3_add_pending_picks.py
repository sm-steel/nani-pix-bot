"""pending_picks: the item a /grantitem player menu will grant (issue #479)

Revision ID: c4f9a2d7e1b3
Revises: b3e8f1a6c4d7
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f9a2d7e1b3"
down_revision: str | Sequence[str] | None = "b3e8f1a6c4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_picks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(length=96), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("pending_picks")
