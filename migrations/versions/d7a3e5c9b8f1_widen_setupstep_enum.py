"""widen setupstep enum

Revision ID: d7a3e5c9b8f1
Revises: c4d9e6b3f2a1
Create Date: 2026-09-13 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7a3e5c9b8f1"
down_revision: str | Sequence[str] | None = "c4d9e6b3f2a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = ("PICKING_METHOD", "AWAITING_PHOTO_CHANGE", "AWAITING_SYNONYM", "CONFIRMING")
# Appends PICKING_SCREENSHOT last, while models/enums.py's SetupStep
# declares it second (right after PICKING_METHOD). That is a deliberate
# divergence, not a bug to fix by reordering: SQLAlchemy's Enum type
# stores and compares member *names*, never the ordinal position, so
# round-tripping is unaffected either way — but this list mirrors the
# ALTER TABLE that already ran against the live DB, and rewriting it to
# match the model's order would desync the file from the ENUM ordinals
# actually deployed there. Expect this to show up as harmless noise if
# `alembic revision --autogenerate` is ever run against this column.
_NEW_VALUES = (*_OLD_VALUES, "PICKING_SCREENSHOT")


def upgrade() -> None:
    """Widen games.setup_step's native ENUM to add PICKING_SCREENSHOT
    (see models/enums.py's SetupStep) — MariaDB has no "add one literal"
    DDL for an existing ENUM column, so the whole allowed-value list has
    to be restated, same as c2a7f0d94b31's PixelStage widening."""
    op.alter_column(
        "games",
        "setup_step",
        existing_type=sa.Enum(*_OLD_VALUES, name="setupstep"),
        type_=sa.Enum(*_NEW_VALUES, name="setupstep"),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Reverts the ENUM's allowed-value list to the old 4-member set.
    Not data-safe if any row's setup_step currently holds
    PICKING_SCREENSHOT — acceptable here since this is a fresh addition
    with no meaningful window where a live row would hold it before a
    downgrade could run."""
    op.alter_column(
        "games",
        "setup_step",
        existing_type=sa.Enum(*_NEW_VALUES, name="setupstep"),
        type_=sa.Enum(*_OLD_VALUES, name="setupstep"),
        existing_nullable=False,
    )
