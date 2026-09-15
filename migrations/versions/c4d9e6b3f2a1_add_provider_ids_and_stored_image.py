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


# --------------------------------------------------------------------
# Safe diagnostics (issue #84).
#
# This migration runs from deploy.yml's `docker compose run --rm` step
# on a GitHub-hosted Actions runner against a public repo, so anything
# it print()s can land in a log anyone can read. Two secrets are in
# scope on its error paths: BOT_TOKEN, which is folded straight into the
# request path of both URLs _fetch_telegram_file_bytes builds, and
# TELEGRAM_PROXY_URL, whose whole authority (user:pass@host:port) httpx
# renders into the ValueError it raises when the value doesn't parse.
#
# THE RULE THESE HELPERS ENFORCE: no text derived from an exception's
# message, and no text derived from TELEGRAM_PROXY_URL's contents, is
# ever printed. A warning is assembled only from (1) string literals
# written in this file, (2) an exception's class name, and (3)
# structured non-text fields: an HTTP status code, type-checked as
# exactly int before use, plus len(rows) and row.id.
#
# One honest caveat about (3): row.id is the single printed value that
# is neither a literal from this file nor guarded by a type check. It
# is the games table's integer primary key, so it cannot carry a
# credential -- but that is an argument about where it comes from,
# not a check this code performs.
#
# WHY NOT REDACT THE MESSAGE INSTEAD. Three earlier attempts did, and
# all three failed the same way. A literal `str(exc).replace(secret,
# ...)`; then a regex anchored on "scheme://"; then a fail-closed guard
# that dropped the message if `secret in message`. Each one has to
# recognise the secret inside the message -- but httpx *reshapes* the
# value on the way in. It percent-encodes (a space becomes %20, '<'
# becomes %3C, '"' becomes %22), it lower-cases the host, and it can
# split a value across a repr. So the string that reaches str(exc) is
# not the string that was written, and a comparison against the value
# as written misses it while the message spells it out. Enumerating the
# reshapings does not fix this: it only moves the failure to the next
# one httpx adds, and a redaction that fails fails OPEN -- it prints the
# secret.
#
# The rule above removes the recognition step entirely. There is no
# comparison that can be wrong, because the secret is never a candidate
# for output in the first place. That is a property of the code's shape
# rather than of a list of inputs someone thought to try, which is why
# it holds under reshapings nobody has seen yet.
# --------------------------------------------------------------------

# --------------------------------------------------------------------
# Why the fetch path catches Exception (issue #87).
#
# Both guarded sites below -- the httpx.Client construction and the
# per-row fetch -- catch Exception rather than a tuple of named types.
# That replaces an enumeration six successive rounds of this file kept
# extending: httpx.HTTPError, then KeyError, then ValueError, then
# TypeError, then httpx.InvalidURL, then ImportError, with issue #87's
# OverflowError due to be the seventh. Every addition was correct, and
# every one of them left the next type live.
#
# THE LIST CANNOT BE COMPLETED BY INSPECTION. A fetch here runs through
# httpx, httpcore, ssl, socket and CPython's C layer, and what those
# raise is neither a documented contract nor anything this project owns.
# OverflowError does not come from httpx at all: a proxy port above
# C-long max parses fine as a Python int, builds a client fine, and then
# fails in _socket.getaddrinfo, which cannot marshal it into a C long.
# No amount of reading httpx's exception hierarchy would have predicted
# an ArithmeticError on this path.
#
# IT ALSO DOES NOT NEED TO BE COMPLETED, because every exception here
# has the same correct disposition. This backfill is best-effort and
# optional: a skipped row keeps original_image NULL, a state the running
# app already handles (jobs/timers.py no-ops on it,
# has_answer_to_reveal returns False). Letting something escape is not
# the safer failure, it is a strictly worse one -- MariaDB auto-commits
# DDL, so the add_column statements in upgrade() are already durable
# while alembic_version is not, and deploy.yml migrates before the bot
# starts, so the next deploy dies on "Duplicate column name" and the bot
# never comes up at all. There is no exception type for which wedging
# every future deploy beats skipping one row.
#
# WHY Exception AND NOT BaseException. KeyboardInterrupt, SystemExit and
# GeneratorExit must still get out: an operator interrupting a running
# migration means stop, and absorbing that into a warn-and-continue
# would keep issuing DML after the request to stop. Catching Exception
# draws exactly that line for free, because those three are precisely
# the BaseException subclasses that sit outside Exception by design. The
# alternative -- `except BaseException` plus a re-raise list -- would
# reintroduce the thing this change removes, a hand-maintained
# enumeration one level up, waiting for the member nobody listed.
# Delegating the boundary to CPython's own hierarchy is the same move as
# the containment rule above: a property of the code's shape rather than
# of a list someone thought to write.
#
# THE COST, ACCEPTED KNOWINGLY. A broad catch can absorb a bug in this
# file -- a typo raising AttributeError, say -- that a narrow one would
# have surfaced as a crash. Two things make that acceptable. The warning
# prints the exception's class name and the row it happened on, so an
# AttributeError on every row reads nothing like a ConnectTimeout on
# one. And even when the fault is this file's own, the right production
# behaviour is still not to wedge the deploy; the bug gets fixed from
# the log instead.
#
# WHAT IS DELIBERATELY STILL UNGUARDED, unchanged by any of this: the
# op.add_column/op.drop_column statements in upgrade(), and the
# bind.execute() UPDATE in the loop below, all sit outside every try. A
# failed DDL or DML statement has no skip-and-continue recovery, and
# completing a migration whose schema change did not apply is worse than
# failing loudly. Widening the catch must never be allowed to creep over
# them.
# --------------------------------------------------------------------

# httpx accepts exactly these proxy schemes (httpx._config.Proxy raises
# ValueError on anything else); socks5/socks5h additionally need the
# optional "socksio" package, which this project does not depend on.
_HTTP_PROXY_SCHEMES = ("http", "https")
_SOCKS_PROXY_SCHEMES = ("socks5", "socks5h")

# The complete range of _describe_proxy_shape. Keeping these as named
# constants is not decoration: it is what makes "the hint cannot carry
# the value" checkable, by a reader and by a test.
_PROXY_SHAPE_UNSET = "TELEGRAM_PROXY_URL is unset"
_PROXY_SHAPE_NO_SCHEME = "TELEGRAM_PROXY_URL has no '<scheme>://' prefix"
_PROXY_SHAPE_HTTP = "TELEGRAM_PROXY_URL's scheme is http:// or https://"
_PROXY_SHAPE_SOCKS = (
    "TELEGRAM_PROXY_URL asks for a SOCKS proxy, which needs the optional "
    "'socksio' package this project does not install"
)
_PROXY_SHAPE_UNKNOWN_SCHEME = "TELEGRAM_PROXY_URL's scheme is none of http/https/socks5/socks5h"
_PROXY_SHAPE_PADDED_SCHEME = (
    "TELEGRAM_PROXY_URL's scheme is padded with whitespace, which httpx "
    "rejects even when the scheme name itself is one it supports"
)
_PROXY_SHAPE_HINTS = (
    _PROXY_SHAPE_UNSET,
    _PROXY_SHAPE_NO_SCHEME,
    _PROXY_SHAPE_HTTP,
    _PROXY_SHAPE_SOCKS,
    _PROXY_SHAPE_UNKNOWN_SCHEME,
    _PROXY_SHAPE_PADDED_SCHEME,
)


def _describe_proxy_shape(raw_proxy: str | None) -> str:
    """Say what is wrong with TELEGRAM_PROXY_URL without repeating any
    of it back. Every return value is one of _PROXY_SHAPE_HINTS above --
    a literal from this file, chosen by classifying the value, never
    built from it. A function whose entire range is a fixed constant set
    cannot emit anything derived from its input, however that input is
    shaped or reshaped.

    Classification only ever *compares*: the text before "://" is
    matched against the scheme allowlist, and the matched entry is
    discarded in favour of the corresponding literal. A value with no
    "://" at all (the single most likely mistyping of .env.example's
    documented "http://USERNAME:PASSWORD@PROXY_HOST:PROXY_PORT", and the
    shape issue #84 was filed about) lands on _PROXY_SHAPE_NO_SCHEME; a
    value whose leading segment happens to be the credentials themselves
    matches no allowlist entry and lands on _PROXY_SHAPE_UNKNOWN_SCHEME.
    Neither path has a branch that can echo.

    The padding check comes before the allowlist on purpose (issue #87's
    folded-in second item). This classifier used to strip the scheme
    before matching, so "  http  ://..." matched the allowlist and the
    operator was told their scheme was http:// or https:// -- true of
    what the classifier had computed, and useless about what had actually
    gone wrong, since httpx does no stripping at all and had rejected the
    value precisely because of the whitespace. Padding dominates the
    scheme name for socks too: "  socks5  ://..." is refused on the
    padding, long before the absent socksio package could matter. Case is
    the one difference that stays tolerated, because httpx tolerates it
    as well -- "HTTP://..." builds a client fine, so reporting it as a
    fault would be the mirror-image mistake."""
    if not raw_proxy:
        return _PROXY_SHAPE_UNSET
    scheme, separator, _ = raw_proxy.partition("://")
    if not separator:
        return _PROXY_SHAPE_NO_SCHEME
    if scheme != scheme.strip():
        return _PROXY_SHAPE_PADDED_SCHEME
    normalised = scheme.casefold()
    if normalised in _HTTP_PROXY_SCHEMES:
        return _PROXY_SHAPE_HTTP
    if normalised in _SOCKS_PROXY_SCHEMES:
        return _PROXY_SHAPE_SOCKS
    return _PROXY_SHAPE_UNKNOWN_SCHEME


def _describe_exception_safely(exc: BaseException) -> str:
    """Summarise an exception without using its message.

    str(exc)/repr(exc) are both off limits here -- httpx.HTTPStatusError
    embeds the full request URL (and therefore BOT_TOKEN) in its
    message, and httpx's proxy ValueError embeds the whole raw
    TELEGRAM_PROXY_URL authority in its own. What is left is still
    enough to act on: the class name alone separates a bad scheme
    (ValueError) from a bad port (InvalidURL) from a SOCKS URL
    (ImportError) from a timeout (ConnectTimeout) from a malformed body
    (JSONDecodeError/TypeError/KeyError).

    The one message-level detail worth keeping is an HTTP status code,
    and it does not need the message at all -- it is an int on the
    response object, so it comes across as structured data rather than
    as text that might have a secret next to it. It is accepted only
    when its type is exactly int; see the comment at the check."""
    summary = type(exc).__name__
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    # `type(...) is int`, not isinstance: httpx.Response does not coerce
    # what it is handed, and isinstance would admit bool and any int
    # subclass -- including one with a hostile __str__ that renders
    # arbitrary text here. Unreachable from configuration, but exact-type
    # is free and keeps the invariant airtight rather than nearly so.
    if type(status_code) is int:
        summary = f"{summary} (HTTP {status_code})"
    return summary


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
    except Exception as exc:
        # A malformed TELEGRAM_PROXY_URL (unknown scheme, invalid port,
        # padded scheme, ...) raises straight out of the client's
        # constructor, before any per-row fetch even starts -- the same
        # "escape upgrade() after the add_column calls already
        # committed" wedge as everything caught inside the loop below,
        # just triggered by client setup instead of a bad file_id, so it
        # gets the same warn-and-skip treatment. Three different types
        # have already been observed reaching this branch over this one
        # environment variable: ValueError for a scheme httpx does not
        # accept, httpx.InvalidURL for a port it cannot parse, and
        # ImportError for a well-formed socks5://, socks5h:// or
        # socks4:// value, since httpx defers SOCKS support to the
        # optional "socksio" package this project does not depend on (no
        # [socks] extra in pyproject.toml or uv.lock). Catching Exception
        # rather than naming those three is the decision recorded in the
        # "Why the fetch path catches Exception" block at the top of this
        # file; there is no fourth type for which failing the migration
        # would be the better outcome.
        #
        # str(exc) is NOT printed here, and must not be reintroduced:
        # httpx renders a proxy URL's password as "[secure]" only once
        # it has successfully parsed a scheme, so the one case that
        # reaches this branch most often -- a TELEGRAM_PROXY_URL missing
        # its scheme entirely -- hands the whole raw authority, password
        # included, straight to str(exc) unmasked. Both halves of the
        # warning below come from literals and a class name instead; see
        # the "Safe diagnostics" block at the top of this file for why
        # sanitising the message text was tried three times and cannot
        # be made reliable.
        print(
            f"WARNING: could not configure the Telegram HTTP client "
            f"({_describe_proxy_shape(proxy)}): "
            f"{_describe_exception_safely(exc)} — skipping live-fetch "
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
            # The failures already observed on this one line span four
            # unrelated hierarchies, which is why it no longer names
            # them: httpx.HTTPError for a network or status failure;
            # json.JSONDecodeError (a ValueError) when a misbehaving
            # proxy answers 2xx with an HTML interstitial rather than
            # Telegram's JSON, which never trips raise_for_status();
            # KeyError or TypeError from the ["result"]["file_path"]
            # lookup when the body is JSON but not the expected shape;
            # httpx.InvalidURL, which is not even a subclass of
            # httpx.HTTPError, when a corrupted file_path puts a
            # non-printable character into the second client.get(...);
            # and OverflowError out of _socket.getaddrinfo when the proxy
            # port exceeds C-long max (issue #87). Catching Exception
            # instead of extending that tuple a seventh time is the
            # decision recorded in the "Why the fetch path catches
            # Exception" block at the top of this file.
            #
            # Note what stays outside the try: the bind.execute() UPDATE
            # below. Only the fetch is best-effort; a failing DML write
            # must still abort, exactly as before.
            try:
                image_bytes = _fetch_telegram_file_bytes(client, bot_token, row.original_file_id)
            except Exception as exc:
                # str(exc)/repr(exc) both contain bot_token here:
                # httpx.HTTPStatusError's message embeds the full
                # request URL ("...for url 'https://api.telegram.org/
                # bot<TOKEN>/getFile'"), and both URLs built in
                # _fetch_telegram_file_bytes fold the token straight
                # into the path. An httpx.ConnectError can carry the
                # proxy host too -- but NOT, despite the obvious guess,
                # via DNS: socket.gaierror renders as "[Errno ...]
                # getaddrinfo failed" and never embeds the hostname, on
                # Linux or Windows. Do not "correct" this comment back
                # to a resolver claim after testing that one and finding
                # it false; the real vector is TLS. For an https://
                # proxy, httpcore's HTTPConnection._connect passes the
                # proxy host as start_tls's server_hostname inside a
                # block that maps OSError to ConnectError, and
                # ssl.SSLCertVerificationError (an OSError subclass)
                # reads "Hostname mismatch, certificate is not valid for
                # '<proxy host>'". httpx's map_httpcore_exceptions
                # carries that text across verbatim, so a proxy with a
                # mismatched certificate reaches this warning as a
                # ConnectError spelling the host out. Neither secret is
                # printed: _describe_exception_safely reports the
                # exception's class and, for an HTTP failure, its status
                # code as a structured int -- see the "Safe diagnostics"
                # block at the top of this file.
                print(
                    f"WARNING: could not backfill game {row.id}: "
                    f"{_describe_exception_safely(exc)} — leaving "
                    f"original_image NULL"
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
