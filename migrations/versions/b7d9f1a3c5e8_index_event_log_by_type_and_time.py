"""index event_log by type and time

A streak probes the group's activity day by day ("did anyone play between
these two dates?"). Without this index the planner walks every row of the
type through ix_event_log_type_actor/_subject to find the range.

Revision ID: b7d9f1a3c5e8
Revises: f6b8d0e2a4c7
Create Date: 2026-10-07 20:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d9f1a3c5e8"
down_revision: str | Sequence[str] | None = "f6b8d0e2a4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_event_log_type_occurred", "event_log", ["event_type", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_event_log_type_occurred", table_name="event_log")
