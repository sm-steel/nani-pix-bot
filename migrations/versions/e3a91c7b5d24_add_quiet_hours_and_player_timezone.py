"""add quiet hours to bot_settings and timezone to players

Revision ID: e3a91c7b5d24
Revises: 6c12be962932
Create Date: 2026-10-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a91c7b5d24"
down_revision: str | Sequence[str] | None = "6c12be962932"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # All nullable — NULL means "quiet hours off" / "timezone never set",
    # so no server_default/backfill is needed for the existing rows.
    op.add_column("bot_settings", sa.Column("quiet_start", sa.Time(), nullable=True))
    op.add_column("bot_settings", sa.Column("quiet_end", sa.Time(), nullable=True))
    op.add_column("bot_settings", sa.Column("quiet_timezone", sa.String(64), nullable=True))
    op.add_column("players", sa.Column("timezone", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("players", "timezone")
    op.drop_column("bot_settings", "quiet_timezone")
    op.drop_column("bot_settings", "quiet_end")
    op.drop_column("bot_settings", "quiet_start")
