"""add hard mode to games

Revision ID: 4b683cb96245
Revises: f8d2a7c9e4b1
Create Date: 2026-09-20 22:16:26.240659

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b683cb96245"
down_revision: str | Sequence[str] | None = "f8d2a7c9e4b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default required: games already has live rows in any real
    # deployment — a NOT NULL column added to a non-empty table needs a
    # backfill value on MariaDB (same reasoning f8d2a7c9e4b1's own
    # bot_settings.autostart_enabled column used).
    op.add_column(
        "games", sa.Column("hard_mode", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    # Nullable, no backfill needed: an existing row simply isn't a
    # hard-mode game.
    op.add_column("games", sa.Column("hard_mode_turn", sa.Integer(), nullable=True))
    op.add_column(
        "games", sa.Column("hard_mode_image_a", sa.LargeBinary(length=2**32 - 1), nullable=True)
    )
    op.add_column(
        "games", sa.Column("hard_mode_image_b", sa.LargeBinary(length=2**32 - 1), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("games", "hard_mode_image_b")
    op.drop_column("games", "hard_mode_image_a")
    op.drop_column("games", "hard_mode_turn")
    op.drop_column("games", "hard_mode")
