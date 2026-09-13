"""add inactivity timers and pinned message

Revision ID: b3c8f5a2d1e6
Revises: d94e2b71a608
Create Date: 2026-09-13 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c8f5a2d1e6"
down_revision: str | Sequence[str] | None = "d94e2b71a608"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # All three are nullable with no application-level default other than
    # None, so — same as f4b2e9c6a1d3's turn/setup timer columns — no
    # server_default is needed even against the live non-empty tables.
    op.add_column("games", sa.Column("inactivity_nudge_at", sa.DateTime(), nullable=True))
    op.add_column("games", sa.Column("inactivity_advance_at", sa.DateTime(), nullable=True))
    op.add_column("bot_settings", sa.Column("pinned_message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("bot_settings", "pinned_message_id")
    op.drop_column("games", "inactivity_advance_at")
    op.drop_column("games", "inactivity_nudge_at")
