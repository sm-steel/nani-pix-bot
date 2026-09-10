"""add turn and setup timers

Revision ID: f4b2e9c6a1d3
Revises: e7c1f9a4b8d2
Create Date: 2026-09-10 05:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b2e9c6a1d3"
down_revision: str | Sequence[str] | None = "e7c1f9a4b8d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # All three are nullable with no application-level default other than
    # None, so — unlike the NOT NULL columns added in earlier migrations —
    # no server_default is needed even against the live non-empty tables.
    op.add_column("games", sa.Column("setup_deadline", sa.DateTime(), nullable=True))
    op.add_column("turn_state", sa.Column("reminder_at", sa.DateTime(), nullable=True))
    op.add_column("turn_state", sa.Column("expiry_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("turn_state", "expiry_at")
    op.drop_column("turn_state", "reminder_at")
    op.drop_column("games", "setup_deadline")
