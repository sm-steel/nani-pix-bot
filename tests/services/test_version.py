from collections.abc import Iterator

import httpx
import pytest
from loguru import logger

from nani_pix_bot.services import version
from nani_pix_bot.services.search import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    # fetch_release_notes is @cache.cached() — see test_cache.py for why
    # this is extra insurance rather than strictly required (the cache is
    # already scoped per-client, and each test below builds its own).
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def records() -> Iterator[list[tuple[str, str]]]:
    """Every log record emitted while the test runs, as (level, message)
    — same shape as services/search/conftest.py's fixture of the same
    name, duplicated locally since that one is scoped to the search/
    test package."""
    captured: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda message: captured.append((message.record["level"].name, message.record["message"])),
        level="DEBUG",
    )
    yield captured
    logger.remove(sink_id)


def test_installed_version_reads_the_distributions_own_metadata(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, str] = {}

    def fake_version(distribution_name: str) -> str:
        seen["name"] = distribution_name
        return "9.9.9"

    monkeypatch.setattr(version, "_installed_version", fake_version)

    assert version.installed_version() == "9.9.9"
    assert seen["name"] == "nani-pix-bot"


async def test_fetch_release_notes_returns_the_release_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/sm-steel/nani-pix-bot/releases/tags/v1.0.2"
        return httpx.Response(200, json={"body": "### Bug Fixes\n\n- fixed a thing"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notes = await version.fetch_release_notes(client, "1.0.2")

    assert notes == "### Bug Fixes\n\n- fixed a thing"


async def test_fetch_release_notes_returns_none_when_body_is_empty() -> None:
    """GitHub allows a release with no written notes — `body` comes back
    null/empty rather than the key being absent."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"body": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notes = await version.fetch_release_notes(client, "1.0.2")

    assert notes is None


async def test_fetch_release_notes_returns_none_when_no_release_matches(
    records: list[tuple[str, str]],
) -> None:
    """A local/unstamped build's version (e.g. "0.1.0") has no matching
    GitHub release — that's an expected, recoverable outcome, not an
    error, so /version still replies with just the version number."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notes = await version.fetch_release_notes(client, "0.1.0")

    assert notes is None
    assert any(level == "WARNING" for level, _ in records)


async def test_fetch_release_notes_reraises_anything_that_is_not_a_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await version.fetch_release_notes(client, "1.0.2")


async def test_fetch_release_notes_is_cached() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"body": "notes"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await version.fetch_release_notes(client, "1.0.2")
        second = await version.fetch_release_notes(client, "1.0.2")

    assert first == second == "notes"
    assert calls["n"] == 1


def test_render_release_notes_html_renders_headings_as_bold() -> None:
    html = version.render_release_notes_html("### Bug Fixes")

    assert "<b>Bug Fixes</b>" in html
    assert "<h3>" not in html


def test_render_release_notes_html_renders_bullet_lists() -> None:
    html = version.render_release_notes_html("- fixed a thing\n- fixed another thing")

    assert "• fixed a thing" in html
    assert "• fixed another thing" in html
    assert "<ul>" not in html
    assert "<li>" not in html


def test_render_release_notes_html_passes_through_supported_inline_formatting() -> None:
    html = version.render_release_notes_html(
        "**bold** and *italic* and `code` and [a link](https://example.com/x)"
    )

    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>code</code>" in html
    assert '<a href="https://example.com/x">a link</a>' in html


def test_render_release_notes_html_passes_through_fenced_code_blocks() -> None:
    html = version.render_release_notes_html("```python\nprint('hi')\n```")

    assert '<pre><code class="language-python">' in html
    assert "print(&#x27;hi&#x27;)" in html or "print('hi')" in html


def test_render_release_notes_html_escapes_literal_entities() -> None:
    html = version.render_release_notes_html("AT&T <script>alert(1)</script>")

    assert "&amp;" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_release_notes_html_drops_images_and_horizontal_rules() -> None:
    html = version.render_release_notes_html("![alt](https://example.com/x.png)\n\n---\n\ntext")

    assert "<img" not in html
    assert "<hr" not in html
    assert "text" in html


def test_render_release_notes_html_never_emits_a_tag_telegram_rejects() -> None:
    """Telegram's HTML parser rejects the *entire* message on any
    unsupported tag — <p>/<h1-6>/<ul>/<li>/<img>/<hr>/<br> all have to be
    gone, not just the ones exercised by the narrower tests above."""
    sample = (
        "# Title\n\n"
        "## Subtitle\n\n"
        "Some paragraph text with a\nsoft break.\n\n"
        "- item one\n- item two\n\n"
        "1. first\n2. second\n\n"
        "> a quote\n\n"
        "![alt](https://example.com/x.png)\n\n"
        "---\n"
    )

    html = version.render_release_notes_html(sample)

    for forbidden in ("<p>", "<h1", "<h2", "<ul>", "<ol>", "<li>", "<img", "<hr", "<br"):
        assert forbidden not in html, f"{forbidden!r} must not appear in Telegram HTML output"


def test_render_release_notes_html_handles_empty_body() -> None:
    assert version.render_release_notes_html("") == ""


def test_render_release_notes_html_truncates_an_overlong_body() -> None:
    huge = "\n".join(f"- change number {i}" for i in range(2000))

    html = version.render_release_notes_html(huge)

    assert len(html) < len(huge)
    assert "…" in html
