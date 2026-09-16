"""add autostart settings and turn timers

Revision ID: f8d2a7c9e4b1
Revises: e5f1c8a2d4b7
Create Date: 2026-09-17 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8d2a7c9e4b1"
down_revision: str | Sequence[str] | None = "e5f1c8a2d4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Both nullable, same as f4b2e9c6a1d3's turn_state columns — no
    # server_default needed even against a live non-empty table. No
    # backfill either: an existing install's current open-or-not turn
    # state just won't have an idle-autostart timer armed until the next
    # natural "turn opens" event, an acceptable one-time gap.
    op.add_column("turn_state", sa.Column("turn_opened_at", sa.DateTime(), nullable=True))
    op.add_column("turn_state", sa.Column("autostart_deadline_at", sa.DateTime(), nullable=True))
    # server_default required: bot_settings already has a live row (the
    # group's current /language choice) — a NOT NULL column added to a
    # non-empty table needs a backfill value on MariaDB.
    op.add_column(
        "bot_settings",
        sa.Column("autostart_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("bot_settings", "autostart_enabled")
    op.drop_column("turn_state", "autostart_deadline_at")
    op.drop_column("turn_state", "turn_opened_at")
