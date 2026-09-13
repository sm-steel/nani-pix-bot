"""add provider ids and stored image

Revision ID: c4d9e6b3f2a1
Revises: b3c8f5a2d1e6
Create Date: 2026-09-13 18:00:00.000000

"""

import os
from collections.abc import Sequence

import httpx
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d9e6b3f2a1"
down_revision: str | Sequence[str] | None = "b3c8f5a2d1e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A length this large just tells MariaDB to pick LONGBLOB over
# BLOB/MEDIUMBLOB — see models/game.py's IMAGE_COLUMN_LENGTH (kept as a
# literal here rather than imported, per this project's migrations
# standing alone as immutable historical scripts).
_IMAGE_COLUMN_LENGTH = 2**32 - 1


def upgrade() -> None:
    """Upgrade schema."""
    # All nullable with no application-level default other than None —
    # same precedent as every other timer/id column added by earlier
    # migrations in this project.
    op.add_column("games", sa.Column("shikimori_id", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("jikan_id", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("tmdb_id", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("screenshot_source", sa.String(16), nullable=True))
    op.add_column("games", sa.Column("original_image", sa.LargeBinary(length=_IMAGE_COLUMN_LENGTH)))

    _backfill_original_image_for_in_flight_games()

    op.drop_column("games", "original_file_id")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("games", sa.Column("original_file_id", sa.String(512), nullable=True))
    op.drop_column("games", "original_image")
    op.drop_column("games", "screenshot_source")
    op.drop_column("games", "tmdb_id")
    op.drop_column("games", "jikan_id")
    op.drop_column("games", "shikimori_id")


def _backfill_original_image_for_in_flight_games() -> None:
    """One-time catch-up for a game that's already SETUP/ACTIVE when
    this migration first runs against a live DB: fetch its existing
    original_file_id's real bytes from Telegram (a plain synchronous
    HTTP call — getFile, then the file download URL it returns) and
    store them in the just-added original_image column, before
    original_file_id is dropped below. Every later run of this
    migration (a fresh DB, or a game that already went through
    activate_game() under the new original_image-only code) finds no
    matching rows and is a no-op.

    Reads BOT_TOKEN/TELEGRAM_PROXY_URL directly from the environment
    (not via config.load_config(), which requires every config field —
    this backfill shouldn't fail a migration run in an environment
    that's otherwise valid but just doesn't have a bot token set, e.g.
    a fresh contributor DB with no in-flight games to backfill
    anyway)."""
    games = sa.table(
        "games",
        sa.column("id", sa.Integer),
        sa.column("status", sa.String),
        sa.column("original_file_id", sa.String),
        sa.column("original_image", sa.LargeBinary),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(games.c.id, games.c.original_file_id).where(
            games.c.status.in_(["SETUP", "ACTIVE"]),
            games.c.original_file_id.is_not(None),
        )
    ).fetchall()
    if not rows:
        return

    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        print(
            f"WARNING: BOT_TOKEN not set — skipping live-fetch backfill for "
            f"{len(rows)} in-flight game(s); their original_image will stay "
            f"NULL until the app itself sets it via a fresh screenshot/pick"
        )
        return

    proxy = os.environ.get("TELEGRAM_PROXY_URL") or None
    with httpx.Client(proxy=proxy, timeout=30.0) as client:
        for row in rows:
            image_bytes = _fetch_telegram_file_bytes(client, bot_token, row.original_file_id)
            bind.execute(
                games.update().where(games.c.id == row.id).values(original_image=image_bytes)
            )
            print(f"Backfilled original_image for game {row.id} ({len(image_bytes)} bytes)")


def _fetch_telegram_file_bytes(client: httpx.Client, bot_token: str, file_id: str) -> bytes:
    get_file_response = client.get(
        f"https://api.telegram.org/bot{bot_token}/getFile", params={"file_id": file_id}
    )
    get_file_response.raise_for_status()
    file_path = get_file_response.json()["result"]["file_path"]
    download_response = client.get(f"https://api.telegram.org/file/bot{bot_token}/{file_path}")
    download_response.raise_for_status()
    return download_response.content
