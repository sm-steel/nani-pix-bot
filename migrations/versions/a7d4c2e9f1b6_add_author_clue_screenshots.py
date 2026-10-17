"""add games.clue_screenshot_mode and clue_author_screenshots, widen setupstep

Revision ID: a7d4c2e9f1b6
Revises: b3e7a1c9d5f2
Create Date: 2026-10-17 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d4c2e9f1b6"
down_revision: str | Sequence[str] | None = "b3e7a1c9d5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMAGE_COLUMN_LENGTH = 2**32 - 1  # LONGBLOB on MariaDB, as games.original_image

# c3f8a1d6e2b9's list, as deployed.
_OLD_SETUP_STEPS = (
    "PICKING_METHOD",
    "AWAITING_PHOTO_CHANGE",
    "AWAITING_SYNONYM",
    "CONFIRMING",
    "PICKING_SCREENSHOT",
    "ASKING_NUMBERS",
    "PICKING_ALIASES",
)
_NEW_SETUP_STEPS = (*_OLD_SETUP_STEPS, "AWAITING_CLUE_PHOTO")


def upgrade() -> None:
    """Upgrade schema. Every existing game reads as Random mode, today's
    behaviour (the server default fills the rows already there)."""
    op.add_column(
        "games",
        sa.Column(
            "clue_screenshot_mode",
            sa.String(length=16),
            nullable=False,
            server_default="random",
        ),
    )
    op.create_table(
        "clue_author_screenshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=True),
        sa.Column("url", sa.String(length=1024), nullable=True),
        sa.Column("image", sa.LargeBinary(length=_IMAGE_COLUMN_LENGTH), nullable=True),
        sa.Column("fingerprint", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_clue_author_screenshots_game_id"), "clue_author_screenshots", ["game_id"]
    )
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
    AWAITING_CLUE_PHOTO at the time — a short-lived step, acceptable here.
    Just drop_table: dropping the game_id index first fails on MariaDB
    (error 1553, the foreign key still needs it; see c8e0a2b4d6f1)."""
    op.alter_column(
        "games",
        "setup_step",
        existing_type=sa.Enum(*_NEW_SETUP_STEPS, name="setupstep"),
        type_=sa.Enum(*_OLD_SETUP_STEPS, name="setupstep"),
        existing_nullable=False,
    )
    op.drop_table("clue_author_screenshots")
    op.drop_column("games", "clue_screenshot_mode")
