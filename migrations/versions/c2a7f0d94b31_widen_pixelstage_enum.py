"""widen pixelstage enum

Revision ID: c2a7f0d94b31
Revises: f4b2e9c6a1d3
Create Date: 2026-09-10 07:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2a7f0d94b31"
down_revision: str | Sequence[str] | None = "f4b2e9c6a1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Widen games.current_stage's native ENUM from the old 4-member
    X10/X8/X5/X2 set to the renamed 5-member STAGE_1..STAGE_5 set (see
    models/enums.py's PixelStage). MariaDB has no "add one literal" DDL
    for an existing ENUM column — the whole allowed-value list has to be
    restated. No data backfill needed: the games table is empty at the
    time of this migration (post prod-cutover flush)."""
    op.alter_column(
        "games",
        "current_stage",
        existing_type=sa.Enum("X10", "X8", "X5", "X2", name="pixelstage"),
        type_=sa.Enum("STAGE_1", "STAGE_2", "STAGE_3", "STAGE_4", "STAGE_5", name="pixelstage"),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Reverts the ENUM's allowed-value list to the old 4-member set.
    Not data-safe if any row's current_stage holds a value only valid
    under the new set (STAGE_3/STAGE_4, or STAGE_1/STAGE_2/STAGE_5 whose
    names no longer exist in the old set either) — fine here since the
    table is empty, but this is not a generally safe downgrade path."""
    op.alter_column(
        "games",
        "current_stage",
        existing_type=sa.Enum(
            "STAGE_1", "STAGE_2", "STAGE_3", "STAGE_4", "STAGE_5", name="pixelstage"
        ),
        type_=sa.Enum("X10", "X8", "X5", "X2", name="pixelstage"),
        existing_nullable=True,
    )
