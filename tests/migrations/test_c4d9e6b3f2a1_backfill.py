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
    assert "400 Bad Request" in output


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
    assert "<bot-token-redacted>" in output
    assert "game 9" in output


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
