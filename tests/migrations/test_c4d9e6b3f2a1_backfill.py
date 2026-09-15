"""Tests for c4d9e6b3f2a1's original_image backfill error handling
(issue #77).

Migrations aren't exercised by the rest of the test suite at all —
tests/conftest.py builds schema straight from the models with
Base.metadata.create_all, never by running Alembic. This file loads the
migration module directly by path and drives its private
_backfill_original_image_for_in_flight_games() against a real Alembic
Operations context bound to an in-memory SQLite connection, so the
function's own op.get_bind() call resolves exactly as it would under a
genuine `alembic upgrade` — without needing the five op.add_column/
op.drop_column DDL calls in upgrade() itself, which are a separate,
already-covered-by-precedent concern (every earlier migration in this
project does the same column adds) and not what issue #77 is about.

Only the backfill loop's new try/except is under test here — the
scenario from the issue: a failing per-row fetch must leave that row's
original_image NULL and let the migration complete, not abort upgrade()
partway through an already-autocommitted DDL sequence.
"""

import importlib.util
import json
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

_MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "c4d9e6b3f2a1_add_provider_ids_and_stored_image.py"
)


def _load_migration_module() -> ModuleType:
    """Import the migration file fresh under a throwaway module name —
    it's a standalone script Alembic loads by path, not a package
    member, so it has to be pulled in by path here too. Not cached in
    sys.modules under its real name, so each test gets its own module
    object and monkeypatching one test's copy can't leak into another."""
    spec = importlib.util.spec_from_file_location("c4d9e6b3f2a1_under_test", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_games_table(metadata: sa.MetaData) -> sa.Table:
    """The post-add_column, pre-drop_column 'games' shape the backfill
    itself queries and updates — id/status/original_file_id/
    original_image are the only columns _backfill_original_image_for_
    in_flight_games touches."""
    return sa.Table(
        "games",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(16)),
        sa.Column("original_file_id", sa.String(512)),
        sa.Column("original_image", sa.LargeBinary),
    )


@pytest.fixture
def games_connection() -> Iterator[tuple[sa.Connection, sa.Table]]:
    """A live Alembic Operations context bound to a real sqlite
    connection, so op.get_bind() inside the migration resolves exactly
    as it does under `alembic upgrade` — see Operations.context in
    alembic/operations/base.py, the same mechanism env.py uses."""
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    games = _make_games_table(metadata)
    metadata.create_all(engine)
    with engine.connect() as conn:
        migration_context = MigrationContext.configure(conn)
        with Operations.context(migration_context):
            yield conn, games


def _image_by_id(conn: sa.Connection, games: sa.Table) -> dict[int, bytes | None]:
    rows = conn.execute(sa.select(games.c.id, games.c.original_image)).fetchall()
    return {row.id: row.original_image for row in rows}


# Every value below is obviously fake and exists only to be asserted
# *absent* from output. Never put a real credential, host or token in
# this file -- the repo is public.
_FAKE_USER = "fakeuser"
# Assembled from parts rather than written as one string literal so this
# obviously-fake value (present only to be asserted absent) doesn't read
# to ruff's S105 heuristic -- or a secret scanner -- as a real hardcoded
# credential.
_FAKE_PASS = "".join(["fake", "pass"])
_FAKE_HOST = "fake-proxy-host.example.invalid"
_FAKE_AUTHORITY_COMPONENTS = (_FAKE_USER, _FAKE_PASS, "fake-proxy-host")

# Built from parts rather than one string literal so an obviously-fake
# placeholder doesn't read to a secret scanner (or ruff's S105) as a
# hardcoded credential.
_FAKE_TOKEN = "-".join(["PLACEHOLDER", "NOT", "A", "REAL", "TOKEN", "123456"])


def _assert_absent(output: str, *components: str) -> None:
    """Assert none of `components` survived into `output` -- comparing
    against a percent-decoded, case-folded copy of it.

    A plain `component in output` check is not good enough here, and
    that inadequacy is the whole history of this bug: httpx reshapes a
    value on its way into an exception message (it percent-encodes a
    space to %20, a '<' to %3C, and lower-cases the host), so a leak can
    be present in substance while absent as a literal substring. The
    test has to normalise the *output* back before looking, or it will
    happily pass over a message that spells the secret out."""
    haystack = urllib.parse.unquote(output).casefold()
    for component in components:
        assert component.casefold() not in haystack, f"{component!r} leaked into: {output!r}"


def _distinctive_fragments(secret: str) -> tuple[str, ...]:
    """The runs of >=6 alphanumerics in `secret`.

    Asserting absence of the secret *as written* is not enough when the
    shape under test deliberately mangles it -- a shape that puts a
    space in the middle of a token contains no copy of the unmangled
    token, so asserting that string's absence would pass without proving
    anything. Splitting on non-alphanumerics gives pieces that survive
    every reshaping httpx applies (percent-encoding replaces exactly the
    non-alphanumerics; case-folding is handled by _assert_absent), so
    they are what a real leak would actually put in the log."""
    fragments = []
    current = ""
    for character in secret:
        if character.isalnum():
            current += character
        else:
            fragments.append(current)
            current = ""
    fragments.append(current)
    return tuple(fragment for fragment in fragments if len(fragment) >= 6)


# The shapes measured in issue #84, each an obviously-fake authority
# with no scheme (or a scheme httpx rejects). httpx reshapes each one
# differently before it reaches str(exc), which is exactly why redaction
# cannot be built on recognising the value as written.
# Each entry carries the exact password that shape uses, so the
# assertion is against what that shape actually contains rather than
# against the canonical spelling -- a shape whose password is mangled
# holds no copy of the unmangled one, and asserting the unmangled one's
# absence would pass without proving anything.
_RESHAPED_PROXY_SHAPES = [
    ("canonical", f"{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:1080", _FAKE_PASS),
    ("trailing-space", f"{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:1080 ", _FAKE_PASS),
    ("leading-space", f" {_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:1080", _FAKE_PASS),
    ("mixed-case", f"{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:1080".upper(), _FAKE_PASS.upper()),
    ("space-in-password", f"{_FAKE_USER}:fake pass@{_FAKE_HOST}:1080", "fake pass"),
    ("angle-in-password", f"{_FAKE_USER}:fake<pass@{_FAKE_HOST}:1080", "fake<pass"),
    ("quote-in-password", f'{_FAKE_USER}:fake"pass@{_FAKE_HOST}:1080', 'fake"pass'),
    ("brace-in-password", f"{_FAKE_USER}:fake{{pass@{_FAKE_HOST}:1080", "fake{pass"),
    ("leading-slashes", f"//{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:1080", _FAKE_PASS),
    ("unknown-scheme", f"socks9://{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:1080", _FAKE_PASS),
]

# The bot-token shapes earlier rounds closed, plus the two a
# scheme-anchored pattern could never close (a token containing "'" or
# ")" truncates its own character class, leaving the tail in the clear).
# Nothing here needs a real token -- the assertion is always absence.
_RESHAPED_TOKEN_SHAPES = [
    ("canonical", _FAKE_TOKEN),
    ("trailing-space", _FAKE_TOKEN + " "),
    ("leading-space", " " + _FAKE_TOKEN),
    ("interior-space", _FAKE_TOKEN.replace("-REAL-", " REAL ")),
    ("hash", _FAKE_TOKEN + "#fragment"),
    ("slash", _FAKE_TOKEN + "/extra"),
    ("double-quote", _FAKE_TOKEN + '"tail'),
    ("newline", _FAKE_TOKEN + "\nsecond-line"),
    ("single-quote", _FAKE_TOKEN + "'tail"),
    ("close-paren", _FAKE_TOKEN + ")tail"),
]


def _install_mock_transport(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Swap a MockTransport-backed client in for the module's own
    httpx.Client call, so the *real* _fetch_telegram_file_bytes runs and
    httpx genuinely renders the token into the exception message the way
    it would in production. The real class is captured first, before the
    name is patched, so the replacement can't recurse into itself."""
    real_client_cls = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"ok": False, "description": "Bad Request: not found"})

    mock_transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        module.httpx, "Client", lambda *args, **kwargs: real_client_cls(transport=mock_transport)
    )


def _seed_one_in_flight_game(conn: sa.Connection, games: sa.Table, game_id: int) -> None:
    conn.execute(
        games.insert(),
        [{"id": game_id, "status": "ACTIVE", "original_file_id": "x", "original_image": None}],
    )
    conn.commit()


def test_a_failing_fetch_leaves_that_row_null_and_completes(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [
            {"id": 1, "status": "ACTIVE", "original_file_id": "good-id", "original_image": None},
            {"id": 2, "status": "SETUP", "original_file_id": "bad-id", "original_image": None},
        ],
    )
    conn.commit()

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")

    def fake_fetch(client: httpx.Client, bot_token: str, file_id: str) -> bytes:
        if file_id == "bad-id":
            request = httpx.Request("GET", "https://api.telegram.org/fake")
            raise httpx.HTTPStatusError(
                "400 Bad Request", request=request, response=httpx.Response(400, request=request)
            )
        return b"real-bytes"

    monkeypatch.setattr(module, "_fetch_telegram_file_bytes", fake_fetch)

    module._backfill_original_image_for_in_flight_games()  # must not raise

    images = _image_by_id(conn, games)
    assert images[1] == b"real-bytes"
    assert images[2] is None

    output = capsys.readouterr().out
    assert "WARNING" in output
    assert "game 2" in output
    # The status code survives as structured data off the response
    # object; the exception's own message text (which would carry the
    # request URL, and BOT_TOKEN inside it) does not -- see issue #84.
    assert "HTTPStatusError (HTTP 400)" in output


def test_a_missing_response_key_is_also_caught(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed getFile response (Telegram's JSON shape not matching
    what _fetch_telegram_file_bytes expects) raises KeyError from
    `.json()["result"]["file_path"]`, not an httpx exception — the
    backfill's except clause has to catch both."""
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [{"id": 3, "status": "ACTIVE", "original_file_id": "weird-id", "original_image": None}],
    )
    conn.commit()

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")

    def fake_fetch(client: httpx.Client, bot_token: str, file_id: str) -> bytes:
        raise KeyError("result")

    monkeypatch.setattr(module, "_fetch_telegram_file_bytes", fake_fetch)

    module._backfill_original_image_for_in_flight_games()  # must not raise

    assert _image_by_id(conn, games)[3] is None


def test_fetch_raises_http_error_on_a_telegram_400() -> None:
    """Confirms the exception type the backfill actually catches
    (httpx.HTTPError) is what a real expired/invalid file_id produces —
    Telegram answers getFile with a non-2xx status, which
    raise_for_status turns into httpx.HTTPStatusError, a subclass of
    httpx.HTTPError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: file not found"})

    module = _load_migration_module()
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.HTTPError),
    ):
        module._fetch_telegram_file_bytes(client, "fake-token", "expired-file-id")


def test_fetch_raises_value_error_on_a_non_json_2xx_body() -> None:
    """A misbehaving proxy/intermediary can answer a 2xx with something
    that isn't Telegram's JSON at all (an HTML interstitial, say) — that
    never trips raise_for_status(), but .json() then raises
    json.JSONDecodeError, a ValueError subclass. This is the "proxy
    hiccup" scenario named in issue #77's own rationale, so the except
    clause has to catch it too."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>proxy error page</html>")

    module = _load_migration_module()
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(json.JSONDecodeError),
    ):
        module._fetch_telegram_file_bytes(client, "fake-token", "some-file-id")


def test_fetch_raises_type_error_on_a_malformed_but_json_2xx_body() -> None:
    """A 2xx body that is valid JSON but whose "result" isn't a dict (a
    malformed-but-technically-JSON success response) raises TypeError
    from the ["file_path"] lookup rather than KeyError or ValueError —
    also has to be caught."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": "not-a-dict"})

    module = _load_migration_module()
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(TypeError),
    ):
        module._fetch_telegram_file_bytes(client, "fake-token", "some-file-id")


def test_fetch_raises_invalid_url_on_a_corrupted_file_path() -> None:
    """A corrupted proxy response can put a non-printable character into
    file_path — the same corrupted-response threat model already
    accepted for a non-JSON or malformed-JSON body — which makes the
    second client.get(...) raise httpx.InvalidURL. Unlike every other
    exception this file catches, InvalidURL's MRO is (InvalidURL,
    Exception, BaseException, object): it is not a subclass of
    httpx.HTTPError, so the except clause has to name it explicitly."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "getFile" in str(request.url):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "bad\x00path"}})
        raise AssertionError("should not reach the download request")

    module = _load_migration_module()
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.InvalidURL),
    ):
        module._fetch_telegram_file_bytes(client, "fake-token", "some-file-id")


def test_the_loop_catches_invalid_url_too(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Confirms the loop itself, not just _fetch_telegram_file_bytes in
    isolation, leaves the row NULL and keeps going for httpx.InvalidURL
    — the one exception type in this set that isn't a subclass of
    anything else already caught, so a fix that widened the tuple
    without this specific member would still wedge a deploy on this
    input."""
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [{"id": 8, "status": "ACTIVE", "original_file_id": "corrupt-id", "original_image": None}],
    )
    conn.commit()

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")

    def fake_fetch(client: httpx.Client, bot_token: str, file_id: str) -> bytes:
        raise httpx.InvalidURL("Invalid non-printable ASCII character in URL")

    monkeypatch.setattr(module, "_fetch_telegram_file_bytes", fake_fetch)

    module._backfill_original_image_for_in_flight_games()  # must not raise

    assert _image_by_id(conn, games)[8] is None
    assert "game 8" in capsys.readouterr().out


def test_a_telegram_error_does_not_leak_the_bot_token_into_the_warning(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """httpx.HTTPStatusError's own message embeds the full request URL,
    and both URLs _fetch_telegram_file_bytes builds have BOT_TOKEN
    folded straight into the path — so printing str(exc) unredacted
    would write a live bot token into deploy.yml's GitHub Actions logs
    on a public repo, on exactly the most likely trigger (an
    expired/invalid file_id producing a Telegram 400). Runs the real
    (unmocked) _fetch_telegram_file_bytes through a fake transport
    swapped in for httpx.Client, so the exception message genuinely
    contains the token the same way it would in production, rather than
    asserting redaction against a message that was never at risk.

    Uses an obviously-fake placeholder token, per this repo's rule
    against ever writing a real credential anywhere — including here,
    where the whole point is proving it does NOT end up in output."""
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [{"id": 9, "status": "ACTIVE", "original_file_id": "expired-id", "original_image": None}],
    )
    conn.commit()

    module = _load_migration_module()
    # Built from parts, not one literal, so this obviously-fake value
    # (used here purely to prove it does NOT end up in output) doesn't
    # itself read to a secret scanner as a hardcoded credential.
    placeholder_token = "-".join(["PLACEHOLDER", "NOT", "A", "REAL", "TOKEN", "123456"])
    monkeypatch.setenv("BOT_TOKEN", placeholder_token)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: file not found"})

    mock_transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client", lambda *args, **kwargs: real_client_cls(transport=mock_transport)
    )

    module._backfill_original_image_for_in_flight_games()  # must not raise

    assert _image_by_id(conn, games)[9] is None

    output = capsys.readouterr().out
    assert placeholder_token not in output
    assert "HTTPStatusError (HTTP 400)" in output
    assert "game 9" in output


def test_a_malformed_proxy_url_skips_the_whole_backfill_and_completes(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed TELEGRAM_PROXY_URL (unknown scheme, bad port, ...)
    raises straight out of httpx.Client's own constructor, before the
    loop -- or even the first row -- is reached. That must not escape
    upgrade() any more than a bad file_id does: warn, skip the whole
    backfill, leave every in-flight row NULL, let the migration
    complete. Uses a real invalid proxy URL against the real
    httpx.Client constructor (not a faked exception), since the point
    is confirming httpx actually raises what this expects it to.

    The proxy URL carries an obviously-fake username and host (an
    "unknown scheme" error embeds the full authority in its message) so
    this can also confirm neither ends up in the warning -- a real
    proxy's host/username are exactly the owned-infrastructure detail
    this project's own convention keeps out of anything public, and
    this migration's warnings can land in public GitHub Actions logs."""
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [{"id": 10, "status": "ACTIVE", "original_file_id": "some-id", "original_image": None}],
    )
    conn.commit()

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")
    monkeypatch.setenv(
        "TELEGRAM_PROXY_URL",
        "socks9://fakeuser:fakepass@fake-proxy-host.example.invalid:1080",
    )

    module._backfill_original_image_for_in_flight_games()  # must not raise

    assert _image_by_id(conn, games)[10] is None

    output = capsys.readouterr().out
    assert "WARNING" in output
    assert "TELEGRAM_PROXY_URL" in output
    assert "fakeuser" not in output
    assert "fake-proxy-host" not in output
    # The scheme was present but is not one httpx accepts, so the
    # warning names that fact from its own literals and reports the
    # exception's class -- never httpx's message, which spells the
    # authority out.
    assert "scheme is none of http/https/socks5/socks5h" in output
    assert "ValueError" in output


def test_a_scheme_less_proxy_url_does_not_leak_the_authority(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """.env.example documents TELEGRAM_PROXY_URL as
    "http://USERNAME:PASSWORD@PROXY_HOST:PROXY_PORT" -- so dropping the
    scheme is the single most likely way to mistype it. httpx does not
    treat a scheme-less value as a URL at all: it fails immediately and
    hands the whole raw string, password included, to str(exc) -- its
    own "[secure]" password masking never even engages, since that
    requires a successfully parsed scheme. Nothing here may reach the
    warning: not the host, not the username, not the password.

    See test_a_reshaped_proxy_url_leaks_no_component_of_the_authority
    for the same guarantee across every reshaping issue #84 measured;
    this one pins the plain scheme-less case on its own."""
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [{"id": 12, "status": "ACTIVE", "original_file_id": "some-id", "original_image": None}],
    )
    conn.commit()

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")
    monkeypatch.setenv(
        "TELEGRAM_PROXY_URL",
        "fakeuser:fakepass@fake-proxy-host.example.invalid:1080",
    )

    module._backfill_original_image_for_in_flight_games()  # must not raise

    assert _image_by_id(conn, games)[12] is None

    output = capsys.readouterr().out
    assert "WARNING" in output
    assert "TELEGRAM_PROXY_URL" in output
    assert "fakeuser" not in output
    assert "fakepass" not in output
    assert "fake-proxy-host" not in output


def test_a_socks_proxy_url_does_not_escape_upgrade(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A well-formed socks5://, socks5h:// or socks4:// TELEGRAM_PROXY_URL
    makes httpx.Client's constructor raise ImportError ("the 'socksio'
    package is not installed") since this project has no [socks] extra
    on httpx -- unrelated to whether the URL itself parses fine. Before
    round 5 this wasn't in the except tuple at all, so it escaped
    upgrade() after the five add_column calls had already committed,
    the exact wedge this ticket exists to close, just via a different
    exception type than Finding A's."""
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [{"id": 13, "status": "ACTIVE", "original_file_id": "some-id", "original_image": None}],
    )
    conn.commit()

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")
    monkeypatch.setenv(
        "TELEGRAM_PROXY_URL",
        "socks5://fakeuser:fakepass@fake-proxy-host.example.invalid:1080",
    )

    module._backfill_original_image_for_in_flight_games()  # must not raise

    assert _image_by_id(conn, games)[13] is None

    output = capsys.readouterr().out
    assert "WARNING" in output
    assert "TELEGRAM_PROXY_URL" in output
    assert "fakeuser" not in output
    assert "fake-proxy-host" not in output


def test_redaction_survives_a_token_with_a_trailing_space(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pins the gap a literal `str(exc).replace(bot_token, ...)` had: a
    misconfigured .env can hand this migration a token with a stray
    trailing space (Compose preserves literal whitespace in .env
    values), which httpx percent-encodes to "...%20" in the URL it
    renders into the exception message -- a literal match against the
    raw (unencoded) bot_token string would miss that and print the
    secret in the clear on exactly the misconfiguration that makes this
    error path run. The warning has to stay safe regardless of how httpx
    rendered the token -- which it now is by never using the message
    text at all (issue #84)."""
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [{"id": 11, "status": "ACTIVE", "original_file_id": "expired-id", "original_image": None}],
    )
    conn.commit()

    module = _load_migration_module()
    # Built from parts (not one literal) for the same S105 reason as the
    # other placeholder-token test, plus a trailing space -- the actual
    # misconfiguration under test -- that must survive monkeypatch.setenv
    # unstripped.
    placeholder_token = "-".join(["PLACEHOLDER", "NOT", "A", "REAL", "TOKEN", "654321"]) + " "
    monkeypatch.setenv("BOT_TOKEN", placeholder_token)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: file not found"})

    mock_transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client
    monkeypatch.setattr(
        module.httpx, "Client", lambda *args, **kwargs: real_client_cls(transport=mock_transport)
    )

    module._backfill_original_image_for_in_flight_games()  # must not raise

    assert _image_by_id(conn, games)[11] is None

    output = capsys.readouterr().out
    assert placeholder_token.strip() not in output
    assert "HTTPStatusError (HTTP 400)" in output
    assert "game 11" in output


def test_the_loop_catches_value_error_and_type_error_too(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The two fetch-level tests above confirm what exception type a
    non-JSON or malformed-JSON 2xx body raises; this confirms the loop
    itself — not just _fetch_telegram_file_bytes in isolation — leaves
    each such row NULL and keeps going, the same guarantee already
    proven for httpx.HTTPError."""
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [
            {"id": 5, "status": "ACTIVE", "original_file_id": "html-id", "original_image": None},
            {"id": 6, "status": "ACTIVE", "original_file_id": "odd-id", "original_image": None},
        ],
    )
    conn.commit()

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")

    def fake_fetch(client: httpx.Client, bot_token: str, file_id: str) -> bytes:
        if file_id == "html-id":
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        raise TypeError("string indices must be integers")

    monkeypatch.setattr(module, "_fetch_telegram_file_bytes", fake_fetch)

    module._backfill_original_image_for_in_flight_games()  # must not raise

    images = _image_by_id(conn, games)
    assert images[5] is None
    assert images[6] is None

    output = capsys.readouterr().out
    assert "game 5" in output
    assert "game 6" in output


def test_a_successful_backfill_is_not_swallowed(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Guards against a too-broad fix that skips every row: a row whose
    fetch succeeds must still be written and logged, not just the
    failure path."""
    conn, games = games_connection
    conn.execute(
        games.insert(),
        [{"id": 4, "status": "ACTIVE", "original_file_id": "good-id", "original_image": None}],
    )
    conn.commit()

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")
    monkeypatch.setattr(module, "_fetch_telegram_file_bytes", lambda client, token, fid: b"bytes")

    module._backfill_original_image_for_in_flight_games()

    assert _image_by_id(conn, games)[4] == b"bytes"
    assert "Backfilled original_image for game 4" in capsys.readouterr().out


# --------------------------------------------------------------------
# issue #84 -- the warnings must be safe by construction, not by
# recognising a secret after the fact.
# --------------------------------------------------------------------


def test_the_exception_summary_never_carries_the_exception_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load-bearing invariant, tested directly rather than through a
    scenario: _describe_exception_safely must build its result out of
    the exception's *type*, never its message. Three previous fixes
    tried to sanitise the message text and each was defeated by a
    reshaping nobody had enumerated; if the text is never used at all,
    there is no reshaping left to be defeated by."""
    module = _load_migration_module()
    sentinel = "-".join(["SENTINEL", "MUST", "NEVER", "BE", "PRINTED"])

    for exc in (
        ValueError(f"Unknown scheme for proxy URL {sentinel}"),
        ImportError(f"Using SOCKS proxy {sentinel}"),
        httpx.InvalidURL(f"Invalid port: {sentinel}"),
        KeyError(sentinel),
        TypeError(sentinel),
    ):
        summary = module._describe_exception_safely(exc)
        assert sentinel not in summary
        assert summary == type(exc).__name__


def test_the_exception_summary_keeps_a_structured_http_status() -> None:
    """The one diagnostic worth keeping from an HTTPStatusError is its
    status code, and it is available as a structured int off the
    response object -- no message text needed, so keeping it costs
    nothing in safety."""
    module = _load_migration_module()
    request = httpx.Request("GET", "https://api.telegram.org/fake")
    exc = httpx.HTTPStatusError(
        "400 Bad Request", request=request, response=httpx.Response(400, request=request)
    )

    assert module._describe_exception_safely(exc) == "HTTPStatusError (HTTP 400)"


@pytest.mark.parametrize(
    ("label", "raw_proxy", "password"), _RESHAPED_PROXY_SHAPES, ids=lambda v: str(v)[:24]
)
def test_the_proxy_shape_hint_is_always_one_of_this_file_s_own_literals(
    label: str, raw_proxy: str, password: str
) -> None:
    """The structural claim, made checkable: whatever TELEGRAM_PROXY_URL
    contains, the hint the warning prints about it is a member of a
    fixed, finite tuple of literals defined in the migration itself. A
    function whose entire range is a constant set cannot emit anything
    derived from its input, so it cannot leak under *any* reshaping --
    that is a property of the code's shape, not of a list of inputs
    somebody thought to try."""
    module = _load_migration_module()

    assert module._describe_proxy_shape(raw_proxy) in module._PROXY_SHAPE_HINTS


@pytest.mark.parametrize(
    ("label", "raw_proxy", "password"), _RESHAPED_PROXY_SHAPES, ids=lambda v: str(v)[:24]
)
def test_a_reshaped_proxy_url_leaks_no_component_of_the_authority(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    label: str,
    raw_proxy: str,
    password: str,
) -> None:
    """Issue #84's measured leak set, driven through the real backfill
    and the real httpx.Client constructor.

    Every one of these is a plausible mistyping of .env.example's
    documented "http://USERNAME:PASSWORD@PROXY_HOST:PROXY_PORT" -- the
    uppercase one needs no typo at all, since the documented
    placeholders are uppercase and httpx lower-cases the host. httpx
    reshapes each differently on the way into str(exc) (percent-encoding
    a space to %20, a '<' to %3C, case-folding the host), which is
    precisely why a redaction built on matching the raw value failed:
    the value that reaches the message is not the value that was
    written."""
    conn, games = games_connection
    _seed_one_in_flight_game(conn, games, 20)

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", raw_proxy)

    module._backfill_original_image_for_in_flight_games()  # must not raise

    assert _image_by_id(conn, games)[20] is None

    output = capsys.readouterr().out
    _assert_absent(output, *_FAKE_AUTHORITY_COMPONENTS, password)
    assert "WARNING" in output
    assert "TELEGRAM_PROXY_URL" in output


@pytest.mark.parametrize(("label", "raw_token"), _RESHAPED_TOKEN_SHAPES, ids=lambda v: str(v)[:24])
def test_a_reshaped_bot_token_never_reaches_the_per_row_warning(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    label: str,
    raw_token: str,
) -> None:
    """The bot-token half of the same guarantee, over every shape
    earlier rounds handled (canonical, leading/trailing/interior space,
    '#', '/', '"', newline) plus the two a scheme-anchored pattern never
    could ("'" and ")" end its character class early, leaving the tail
    of the token in the clear). Runs the real _fetch_telegram_file_bytes
    against a simulated Telegram 400 so the token is genuinely rendered
    into the exception message, the same as in production."""
    conn, games = games_connection
    _seed_one_in_flight_game(conn, games, 21)

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", raw_token)
    _install_mock_transport(module, monkeypatch, status=400)

    module._backfill_original_image_for_in_flight_games()  # must not raise

    assert _image_by_id(conn, games)[21] is None

    output = capsys.readouterr().out
    # Against the fragments of the shape actually used, not of the
    # unmangled placeholder -- a shape that puts a space inside the
    # token contains no copy of the unmangled one, so asserting that
    # string's absence would pass vacuously.
    _assert_absent(output, *_distinctive_fragments(raw_token))
    assert "WARNING" in output
    assert "game 21" in output


def test_an_operator_can_still_tell_the_proxy_failure_modes_apart(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Redacting hard is only acceptable if the warning still localises
    the fault. Each distinct misconfiguration has to produce a
    distinguishable warning -- a missing scheme, an unsupported scheme,
    a SOCKS scheme (which needs a package this project doesn't install)
    and a bad port are four different things to go fix."""
    conn, games = games_connection
    _seed_one_in_flight_game(conn, games, 22)
    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", "fake-token-for-test")

    warnings = {}
    for label, raw_proxy in (
        ("no-scheme", f"{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:1080"),
        ("unknown-scheme", f"socks9://{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:1080"),
        ("socks", f"socks5://{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:1080"),
        ("bad-port", f"http://{_FAKE_USER}:{_FAKE_PASS}@{_FAKE_HOST}:not-a-port"),
    ):
        monkeypatch.setenv("TELEGRAM_PROXY_URL", raw_proxy)
        module._backfill_original_image_for_in_flight_games()  # must not raise
        warnings[label] = capsys.readouterr().out

    assert len(set(warnings.values())) == len(warnings)
    assert "no '<scheme>://' prefix" in warnings["no-scheme"]
    assert "socksio" in warnings["socks"]
    assert "ImportError" in warnings["socks"]
    assert "ValueError" in warnings["unknown-scheme"]
    assert "InvalidURL" in warnings["bad-port"]
    for output in warnings.values():
        _assert_absent(output, *_FAKE_AUTHORITY_COMPONENTS)


def test_a_per_row_telegram_400_is_still_identifiable_as_a_400(
    games_connection: tuple[sa.Connection, sa.Table],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The per-row half of the same requirement: an expired file_id is
    the single most likely trigger for this whole error path, and an
    operator has to be able to see that it was an HTTP 400 rather than a
    timeout or a malformed body -- without the request URL (and the
    token in it) coming along for the ride."""
    conn, games = games_connection
    _seed_one_in_flight_game(conn, games, 23)

    module = _load_migration_module()
    monkeypatch.setenv("BOT_TOKEN", _FAKE_TOKEN)
    _install_mock_transport(module, monkeypatch, status=400)

    module._backfill_original_image_for_in_flight_games()  # must not raise

    output = capsys.readouterr().out
    assert "HTTPStatusError (HTTP 400)" in output
    assert "game 23" in output
    _assert_absent(output, _FAKE_TOKEN, "api.telegram.org")
