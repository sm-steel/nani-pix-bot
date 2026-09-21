"""add mal_credentials and pending_mal_link tables

Revision ID: 6a66b9e99547
Revises: a98069983e15
Create Date: 2026-09-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6a66b9e99547"
down_revision: str | Sequence[str] | None = "a98069983e15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "mal_credentials",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("access_token", sa.String(length=512), nullable=False),
        sa.Column("refresh_token", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("mal_username", sa.String(length=64), nullable=True),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["telegram_user_id"], ["players.telegram_user_id"]),
        sa.PrimaryKeyConstraint("telegram_user_id"),
    )
    op.create_table(
        "pending_mal_link",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["telegram_user_id"], ["players.telegram_user_id"]),
        sa.PrimaryKeyConstraint("telegram_user_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("pending_mal_link")
    op.drop_table("mal_credentials")
