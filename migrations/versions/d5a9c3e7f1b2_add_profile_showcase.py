"""add profile_showcase

Revision ID: d5a9c3e7f1b2
Revises: c4f8b2d6e1a7
Create Date: 2026-10-11 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a9c3e7f1b2"
down_revision: str | Sequence[str] | None = "c4f8b2d6e1a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "profile_showcase",
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.telegram_user_id"],
            name=op.f("fk_profile_showcase_player_id_players"),
        ),
        sa.PrimaryKeyConstraint("player_id", "kind", "ref", name=op.f("pk_profile_showcase")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("profile_showcase")
