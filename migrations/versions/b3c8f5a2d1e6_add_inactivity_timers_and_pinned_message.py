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

    # One-time catch-up for a game that's already ACTIVE when this
    # migration first runs against a live DB: back-date
    # inactivity_advance_at to right now, so the bot's rearm-on-startup
    # (jobs/timers.py's rearm_pending_timeouts) schedules it as already
    # overdue and the real inactivity_advance_job_callback fires once,
    # almost immediately after this deploy — handing that game a fresh,
    # correct nudge/advance window from then on, rather than leaving it
    # silently opted out of the new mechanic until its next guess.
    # inactivity_nudge_at is deliberately left NULL here (not
    # back-dated), so no redundant nudge message fires moments before
    # the advance. "IS NULL" makes this a no-op on every later run of
    # this migration against a fresh DB or one where activate_game()
    # already set this column normally — it only ever matches a game
    # that predates this migration.
    op.execute(
        "UPDATE games SET inactivity_advance_at = UTC_TIMESTAMP() "
        "WHERE status = 'ACTIVE' AND inactivity_advance_at IS NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("bot_settings", "pinned_message_id")
    op.drop_column("games", "inactivity_advance_at")
    op.drop_column("games", "inactivity_nudge_at")
