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
    """Downgrade schema.

    Not data-safe: re-adding original_file_id restores an empty column,
    not the file_id that used to live there, and dropping original_image
    destroys whatever bytes were backfilled or written since — bytes are
    not invertible back into the Telegram file_id that produced them.
    Same posture as d7a3e5c9b8f1's own honest downgrade() comment."""
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
            # An expired/invalid file_id (Telegram 400s), a proxy hiccup,
            # a timeout, or a file over 20MB must not escape this loop:
            # MariaDB auto-commits DDL, so the add_column calls above are
            # already durable, and an uncaught raise here would abort
            # upgrade() before alembic_version is bumped — the next
            # deploy would then re-run this migration and die on
            # "Duplicate column name" against a schema that's already
            # been changed. Leaving one row's original_image NULL is a
            # recoverable, already-handled state (jobs/timers.py no-ops
            # on it, has_answer_to_reveal returns False); a half-applied
            # migration wedging every future deploy is not.
            #
            # ValueError and TypeError are in this set for the same
            # "proxy hiccup" reason httpx.HTTPError is: a misbehaving
            # proxy/intermediary can answer with a 2xx whose body isn't
            # Telegram's JSON at all (an HTML interstitial, say) — that
            # never trips raise_for_status(), but .json() then raises
            # json.JSONDecodeError (a ValueError subclass). A 2xx body
            # that *is* JSON but whose "result" isn't a dict (a
            # malformed-but-technically-JSON success response) raises
            # TypeError from the ["file_path"] lookup instead. httpx.
            # InvalidURL covers the same corrupted-proxy-response shape
            # one step later: a file_path containing a non-printable
            # character makes the second client.get(...) below raise
            # InvalidURL, which — unlike every other exception caught
            # here — is not a subclass of httpx.HTTPError, so it needs
            # naming explicitly. All of these are the same
            # deploy-wedging shape as the network failures above, so
            # they get the same fate: skip this row, keep the migration
            # moving.
            try:
                image_bytes = _fetch_telegram_file_bytes(client, bot_token, row.original_file_id)
            except (
                httpx.HTTPError,
                httpx.InvalidURL,
                KeyError,
                ValueError,
                TypeError,
            ) as exc:
                # str(exc) can itself contain bot_token: httpx.
                # HTTPStatusError's message embeds the full request URL
                # ("...for url 'https://api.telegram.org/bot<TOKEN>/
                # getFile'"), and both URLs built in
                # _fetch_telegram_file_bytes have the token folded
                # straight into the path. This migration runs via
                # `docker compose run --rm` from deploy.yml on a
                # GitHub-hosted Actions runner against this public
                # repo, so an unredacted print here would write a live
                # bot token into publicly-readable Actions logs — on
                # exactly the most likely trigger (an expired file_id
                # producing a Telegram 400). repr(exc) has the same
                # problem, so the token is stripped out of the message
                # text itself rather than switched to a different
                # rendering of the same exception.
                safe_message = str(exc).replace(bot_token, "<bot-token-redacted>")
                print(
                    f"WARNING: could not backfill game {row.id}: {safe_message} — "
                    f"leaving original_image NULL"
                )
                continue
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
