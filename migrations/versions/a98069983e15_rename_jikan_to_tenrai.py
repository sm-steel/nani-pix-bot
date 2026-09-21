"""rename jikan to tenrai

Revision ID: a98069983e15
Revises: 4b683cb96245
Create Date: 2026-09-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a98069983e15"
down_revision: str | Sequence[str] | None = "4b683cb96245"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Jikan is shutting down and is replaced by Tenrai, a
    schema-compatible provider (see the tenrai-migration plan) — rename
    the column and rewrite every existing row's provider string. The id
    values themselves need no transformation: Jikan and Tenrai both key
    off the same MyAnimeList catalog id."""
    op.alter_column("games", "jikan_id", new_column_name="tenrai_id", existing_type=sa.Integer())
    op.execute("UPDATE games SET source = 'tenrai' WHERE source = 'jikan'")
    op.execute("UPDATE games SET screenshot_source = 'tenrai' WHERE screenshot_source = 'jikan'")
    op.execute(
        "UPDATE games SET screenshot_picker_provider = 'tenrai' "
        "WHERE screenshot_picker_provider = 'jikan'"
    )


def downgrade() -> None:
    """Reverse in the opposite order: fold the provider strings back to
    "jikan" before renaming the column back, so an in-flight
    `screenshot_picker_provider`/`screenshot_source` value never briefly
    names a column that isn't there yet."""
    op.execute(
        "UPDATE games SET screenshot_picker_provider = 'jikan' "
        "WHERE screenshot_picker_provider = 'tenrai'"
    )
    op.execute("UPDATE games SET screenshot_source = 'jikan' WHERE screenshot_source = 'tenrai'")
    op.execute("UPDATE games SET source = 'jikan' WHERE source = 'tenrai'")
    op.alter_column("games", "tenrai_id", new_column_name="jikan_id", existing_type=sa.Integer())
