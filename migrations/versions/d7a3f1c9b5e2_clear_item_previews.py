"""clear item_previews

The preview cache holds Telegram file_ids of renders on the old drawn
sample scene; previews now use the sample anime screenshot (#424), and
win effects and banners are drawn on the viewer (#427). Emptying it makes
every preview render once more, on demand. A cache, so downgrade has
nothing to restore.

Revision ID: d7a3f1c9b5e2
Revises: e2a4c6b8d0f3
Create Date: 2026-10-14 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d7a3f1c9b5e2"
down_revision: str | Sequence[str] | None = "e2a4c6b8d0f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM item_previews")


def downgrade() -> None:
    """Downgrade schema."""
