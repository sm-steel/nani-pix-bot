"""add stage_config table and games_enabled

Revision ID: d94e2b71a608
Revises: c2a7f0d94b31
Create Date: 2026-09-12 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d94e2b71a608"
down_revision: str | Sequence[str] | None = "c2a7f0d94b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_STAGE_CONFIG = [
    {"stage": "STAGE_1", "target_width": 64, "wrong_guess_limit": 1},
    {"stage": "STAGE_2", "target_width": 80, "wrong_guess_limit": 1},
    {"stage": "STAGE_3", "target_width": 128, "wrong_guess_limit": 2},
    {"stage": "STAGE_4", "target_width": 192, "wrong_guess_limit": 3},
    {"stage": "STAGE_5", "target_width": 512, "wrong_guess_limit": 3},
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "stage_config",
        sa.Column(
            "stage",
            sa.Enum("STAGE_1", "STAGE_2", "STAGE_3", "STAGE_4", "STAGE_5", name="pixelstage"),
            nullable=False,
        ),
        sa.Column("target_width", sa.Integer(), nullable=False),
        sa.Column("wrong_guess_limit", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("stage"),
    )
    op.bulk_insert(
        sa.table(
            "stage_config",
            sa.column("stage", sa.String()),
            sa.column("target_width", sa.Integer()),
            sa.column("wrong_guess_limit", sa.Integer()),
        ),
        _SEED_STAGE_CONFIG,
    )
    # server_default required: bot_settings already has a live row
    # (the group's current /language choice) — a NOT NULL column added
    # to a non-empty table needs a backfill value on MariaDB.
    op.add_column(
        "bot_settings",
        sa.Column("games_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("bot_settings", "games_enabled")
    op.drop_table("stage_config")
