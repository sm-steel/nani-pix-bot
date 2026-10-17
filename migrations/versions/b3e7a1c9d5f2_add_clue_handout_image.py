"""add clue_screenshot_handouts.image

Revision ID: b3e7a1c9d5f2
Revises: 4d7b29a0997a
Create Date: 2026-10-17 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e7a1c9d5f2"
down_revision: str | Sequence[str] | None = "4d7b29a0997a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMAGE_COLUMN_LENGTH = 2**32 - 1  # LONGBLOB on MariaDB, as games.original_image


def upgrade() -> None:
    """Keep the full-quality original of each handed-out screenshot for the album."""
    op.add_column(
        "clue_screenshot_handouts",
        sa.Column("image", sa.LargeBinary(length=_IMAGE_COLUMN_LENGTH), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("clue_screenshot_handouts", "image")
