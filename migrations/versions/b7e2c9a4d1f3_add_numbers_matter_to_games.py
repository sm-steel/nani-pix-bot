"""add numbers_matter to games, widen setupstep with ASKING_NUMBERS

Revision ID: b7e2c9a4d1f3
Revises: ad3865cad46c
Create Date: 2026-10-10 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2c9a4d1f3"
down_revision: str | Sequence[str] | None = "ad3865cad46c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# d7a3e5c9b8f1's list, as deployed (see its note on member order).
_OLD_SETUP_STEPS = (
    "PICKING_METHOD",
    "AWAITING_PHOTO_CHANGE",
    "AWAITING_SYNONYM",
    "CONFIRMING",
    "PICKING_SCREENSHOT",
)
_NEW_SETUP_STEPS = (*_OLD_SETUP_STEPS, "ASKING_NUMBERS")


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, no backfill: NULL is "the creator was never asked", which
    # matching reads as "numbers don't matter" — exactly how every
    # existing game has been played.
    op.add_column("games", sa.Column("numbers_matter", sa.Boolean(), nullable=True))
    # MariaDB can't add one literal to an existing ENUM, so the whole list
    # is restated (same as d7a3e5c9b8f1).
    op.alter_column(
        "games",
        "setup_step",
        existing_type=sa.Enum(*_OLD_SETUP_STEPS, name="setupstep"),
        type_=sa.Enum(*_NEW_SETUP_STEPS, name="setupstep"),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema. Not data-safe for a SETUP game sitting on
    ASKING_NUMBERS at the time — a short-lived step, acceptable here."""
    op.alter_column(
        "games",
        "setup_step",
        existing_type=sa.Enum(*_NEW_SETUP_STEPS, name="setupstep"),
        type_=sa.Enum(*_OLD_SETUP_STEPS, name="setupstep"),
        existing_nullable=False,
    )
    op.drop_column("games", "numbers_matter")
