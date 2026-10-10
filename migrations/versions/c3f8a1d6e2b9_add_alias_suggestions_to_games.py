"""add alias_suggestions to games, widen setupstep with PICKING_ALIASES

Revision ID: c3f8a1d6e2b9
Revises: b7e2c9a4d1f3
Create Date: 2026-10-10 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8a1d6e2b9"
down_revision: str | Sequence[str] | None = "b7e2c9a4d1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# b7e2c9a4d1f3's list, as deployed.
_OLD_SETUP_STEPS = (
    "PICKING_METHOD",
    "AWAITING_PHOTO_CHANGE",
    "AWAITING_SYNONYM",
    "CONFIRMING",
    "PICKING_SCREENSHOT",
    "ASKING_NUMBERS",
)
_NEW_SETUP_STEPS = (*_OLD_SETUP_STEPS, "PICKING_ALIASES")


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, no backfill: a game set up before this has no suggestions.
    op.add_column("games", sa.Column("alias_suggestions", sa.JSON(), nullable=True))
    # MariaDB can't add one literal to an existing ENUM (see d7a3e5c9b8f1).
    op.alter_column(
        "games",
        "setup_step",
        existing_type=sa.Enum(*_OLD_SETUP_STEPS, name="setupstep"),
        type_=sa.Enum(*_NEW_SETUP_STEPS, name="setupstep"),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema. Not data-safe for a SETUP game sitting on
    PICKING_ALIASES at the time — a short-lived step, acceptable here."""
    op.alter_column(
        "games",
        "setup_step",
        existing_type=sa.Enum(*_NEW_SETUP_STEPS, name="setupstep"),
        type_=sa.Enum(*_OLD_SETUP_STEPS, name="setupstep"),
        existing_nullable=False,
    )
    op.drop_column("games", "alias_suggestions")
