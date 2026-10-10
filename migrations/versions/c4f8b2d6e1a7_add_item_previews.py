"""add item_previews

Revision ID: c4f8b2d6e1a7
Revises: b3e7a9c5d2f4
Create Date: 2026-10-10 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f8b2d6e1a7"
down_revision: str | Sequence[str] | None = "b3e7a9c5d2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "item_previews",
        sa.Column("item_id", sa.String(length=96), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("file_id", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_item_previews")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("item_previews")
