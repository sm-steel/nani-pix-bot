"""add provider ids and stored image

Revision ID: c4d9e6b3f2a1
Revises: b3c8f5a2d1e6
Create Date: 2026-09-13 18:00:00.000000

"""

import os
import re
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


def _redact_secrets(exc: Exception, known_secret: str | None) -> str:
    """Scrub anything an exception message from this backfill could
    carry that has no business in a public place, before it ever
    reaches a print() that deploy.yml's `docker compose run --rm` step
    would otherwise write straight into a public GitHub Actions log.
    Two distinct things end up in a URL's text here: a Telegram bot
    token folded into a request path (".../bot<TOKEN>/getFile") when a
    per-row fetch fails, and TELEGRAM_PROXY_URL's own authority
    (scheme://user:pass@host:port) when it fails to parse -- httpx
    already masks that password as "[secure]", but the proxy username
    and host are exactly the owned-infrastructure detail this project's
    own convention (aliases like "amsterdam", placeholders like
    PROXY_HOST) otherwise keeps out of anything public, and printing
    them for real on a misconfiguration would undo that at the one
    moment it matters.

    The primary defence is pattern-based: everything after a
    "scheme://" up to the next whitespace, single-quote, double-quote,
    or close-paren is replaced wholesale, scheme kept -- deliberately
    NOT stopping at the next '/', since the sensitive text (a token, an
    authority) can be anywhere in the rest of the URL, path included,
    and a message with no URL in it at all (e.g. httpx's own "Invalid
    port: '...'") passes through untouched since nothing matches.

    That pattern only fires on a "scheme://" anchor, though, and a
    TELEGRAM_PROXY_URL missing its scheme entirely -- the single most
    likely way to mistype .env.example's documented
    "http://USERNAME:PASSWORD@PROXY_HOST:PROXY_PORT" shape -- is not a
    URL by this pattern's definition at all. httpx still fails on it,
    but hands the whole raw authority, password included (its "[secure]"
    masking never engages without a scheme to parse), straight to
    str(exc). Rather than keep enumerating every shape a
    credential-bearing string can take without a scheme, known_secret
    is a second, fail-closed layer: pass the raw environment value the
    caller already has in scope (bot_token, or TELEGRAM_PROXY_URL's raw
    value), and if it's still sitting in the message after the
    pattern-based pass, discard the message entirely and report only
    the exception's type -- which still tells the operator *that*
    something failed and roughly *what kind* (ImportError, ValueError,
    InvalidURL, ...), without risking the raw value under any input
    the pattern above didn't anticipate."""
    message = str(exc)
    redacted = re.sub(
        r"([A-Za-z][A-Za-z0-9+.-]*)://[^\s'\")]+",
        lambda m: m.group(1) + "://<redacted>",
        message,
    )
    if known_secret and known_secret in redacted:
        return type(exc).__name__
    return redacted


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
    try:
        client = httpx.Client(proxy=proxy, timeout=30.0)
    except (ValueError, httpx.InvalidURL, ImportError) as exc:
        # A malformed TELEGRAM_PROXY_URL (unknown scheme, invalid port,
        # ...) raises straight out of the client's constructor, before
        # any per-row fetch even starts -- the same "escape upgrade()
        # after the add_column calls already committed" wedge as
        # everything caught inside the loop below, just triggered by
        # client setup instead of a bad file_id, so it gets the same
        # warn-and-skip treatment. ImportError covers a well-formed
        # socks5://, socks5h:// or socks4:// value: httpx defers to the
        # optional "socksio" package for SOCKS support, which is not a
        # dependency of this project (no [socks] extra in pyproject.toml
        # or uv.lock), so any such value raises here, unrelated to
        # whether the URL itself is otherwise valid.
        #
        # httpx renders a proxy URL's password as "[secure]" in its own
        # exception text, but only once it has successfully parsed a
        # scheme -- a TELEGRAM_PROXY_URL missing its scheme entirely
        # (the single most likely way to mistype .env.example's
        # documented "http://USERNAME:PASSWORD@PROXY_HOST:PROXY_PORT"
        # shape) hands the whole raw authority, password included,
        # straight to str(exc), unmasked. _redact_secrets's known_secret
        # fallback is what actually closes that -- see its own
        # docstring -- rather than the pattern-based half alone, which
        # only fires on a "scheme://" it can anchor on.
        safe_message = _redact_secrets(exc, known_secret=proxy)
        print(
            f"WARNING: could not configure the Telegram HTTP client from "
            f"TELEGRAM_PROXY_URL: {safe_message} — skipping live-fetch "
            f"backfill for {len(rows)} in-flight game(s); their "
            f"original_image will stay NULL until the app itself sets it "
            f"via a fresh screenshot/pick"
        )
        return

    with client:
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
                # text itself; see _redact_secrets's own docstring
                # for why that's pattern-based rather than a literal
                # substring match against bot_token.
                safe_message = _redact_secrets(exc, known_secret=bot_token)
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
