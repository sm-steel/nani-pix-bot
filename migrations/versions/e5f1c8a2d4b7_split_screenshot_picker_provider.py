"""split screenshot picker provider out of screenshot_source

Revision ID: e5f1c8a2d4b7
Revises: d7a3e5c9b8f1
Create Date: 2026-09-14 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f1c8a2d4b7"
down_revision: str | Sequence[str] | None = "d7a3e5c9b8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same width as games.screenshot_source, which this column splits in
# two — kept as a literal rather than imported from models/game.py's
# SCREENSHOT_SOURCE_LENGTH, per this project's migrations standing alone
# as immutable historical scripts (same precedent as c4d9e6b3f2a1's
# _IMAGE_COLUMN_LENGTH).
_PROVIDER_COLUMN_LENGTH = 16

_games = sa.table(
    "games",
    sa.column("status", sa.String),
    sa.column("setup_step", sa.String),
    sa.column("original_image", sa.LargeBinary),
    sa.column("screenshot_source", sa.String),
    sa.column("screenshot_picker_provider", sa.String),
)


def upgrade() -> None:
    """Give the screenshot picker's own state a column of its own.

    `games.screenshot_source` used to carry two unrelated meanings:
    which provider's `*_id` backs `original_image`, *and* which provider
    the screenshot picker is currently resolving (set the moment a
    source button was tapped, long before any image existed). See
    models/game.py for the two meanings as they now stand."""
    op.add_column(
        "games",
        sa.Column("screenshot_picker_provider", sa.String(_PROVIDER_COLUMN_LENGTH), nullable=True),
    )

    bind = op.get_bind()
    # Hand the picker meaning over first. A SETUP row on the
    # PICKING_SCREENSHOT step is the only place the old column could
    # still have been carrying it: any other step is either before the
    # picker or past it, and an ACTIVE/WON/UNSOLVED game is long past
    # it, so there the old value is already pure provenance. (This does
    # over-approximate by one case — a same-provider gallery, which the
    # new code leaves unset — but no column records which gallery is on
    # screen, and the result is exactly today's behavior for that one
    # in-flight row: a stray typed message gets searched.)
    bind.execute(
        _games.update()
        .where(
            _games.c.status == "SETUP",
            _games.c.setup_step == "PICKING_SCREENSHOT",
            _games.c.screenshot_source.is_not(None),
        )
        .values(screenshot_picker_provider=_games.c.screenshot_source)
    )
    # ...then stop the old column from claiming an image that isn't
    # there. Without this, a starter caught mid-picker by the deploy
    # keeps a screenshot_source naming a provider that backs nothing,
    # and the preview's "Change image" would offer to re-open that
    # provider's gallery for an image it never picked.
    bind.execute(
        _games.update()
        .where(_games.c.status == "SETUP", _games.c.original_image.is_(None))
        .values(screenshot_source=None)
    )


def downgrade() -> None:
    """Fold the picker meaning back into the shared column before
    dropping the new one, so a SETUP game mid-picker still routes its
    next typed message under the old single-column code."""
    bind = op.get_bind()
    bind.execute(
        _games.update()
        .where(_games.c.status == "SETUP", _games.c.screenshot_picker_provider.is_not(None))
        .values(screenshot_source=_games.c.screenshot_picker_provider)
    )
    op.drop_column("games", "screenshot_picker_provider")
