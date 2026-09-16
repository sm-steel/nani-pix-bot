"""Running-version lookup and GitHub release-notes rendering for the
/version command — see commands/version.py for the handler itself.

Release notes are GitHub-flavored Markdown; Telegram's `parse_mode="HTML"`
only understands a narrow entity subset (no `<h1-6>`, `<p>`, `<ul>/<li>`,
`<img>`, `<hr>`, `<br>` — an unsupported tag makes Telegram reject the
*entire* message). `_TelegramHTMLRenderer` below overrides just the
mistune renderer methods that would otherwise emit one of those; every
other construct (`<strong>`, `<em>`, `<a href>`, `<code>`, `<pre>`,
`<del>`, `<blockquote>`) mistune already emits in a form Telegram
accepts as-is."""

from http import HTTPStatus
from importlib.metadata import version as _installed_version
from typing import Any

import httpx
import mistune
from loguru import logger

from nani_pix_bot.services.search import cache
from nani_pix_bot.services.search.rest import RestApi, get_json

_DISTRIBUTION_NAME = "nani-pix-bot"

_GITHUB_REQUEST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": _DISTRIBUTION_NAME,
}
_GITHUB_API = RestApi(name="GitHub", headers=_GITHUB_REQUEST_HEADERS)
_RELEASE_URL_TEMPLATE = (
    "https://api.github.com/repos/sm-steel/nani-pix-bot/releases/tags/v{version}"
)

# Telegram rejects a text message outright past 4096 characters. This
# budget is applied to the *raw Markdown* before rendering, not to the
# rendered HTML: truncating already-rendered HTML risks cutting a tag in
# half (an unclosed `<b>` gets the whole reply rejected as unparsable),
# while mistune always emits well-formed, self-closed tags for whatever
# Markdown fragment survives a cut — even a truncated ``` fence closes
# cleanly at end-of-input. 3000 leaves ample headroom under 4096 for the
# version line, HTML tag overhead, and the caller's own template text.
_MAX_RELEASE_NOTES_MARKDOWN_CHARS = 3000
_TRUNCATION_MARKER = "\n\n…"


def installed_version() -> str:
    """The version stamped into this build's package metadata at release
    time (see pyproject.toml's `[tool.semantic_release] version_toml`).
    Reads back "0.1.0" for an unstamped local dev checkout."""
    return _installed_version(_DISTRIBUTION_NAME)


@cache.cached(ttl=3600.0)
async def fetch_release_notes(client: httpx.AsyncClient, version: str) -> str | None:
    """Fetches the GitHub release body for tag `v{version}`. Returns
    None if no such release exists — an expected outcome for an
    unstamped local build, or a release published with no written
    notes, not an error — logged at WARNING since it's a rejected-but-
    recoverable situation the /version reply degrades gracefully around.
    Cached for an hour: a published release's notes never change, so
    this just caps how often repeated /version calls hit GitHub's API
    (60 req/hr unauthenticated, shared with everything else proxied
    through amsterdam)."""
    url = _RELEASE_URL_TEMPLATE.format(version=version)
    try:
        body = await get_json(_GITHUB_API, client, url, {})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == HTTPStatus.NOT_FOUND:
            logger.warning("No GitHub release found for v{} — /version will omit notes", version)
            return None
        raise
    return body.get("body") or None


def render_release_notes_html(markdown_body: str) -> str:
    """Converts a GitHub release body (GFM) into Telegram-HTML-safe
    text. See the module docstring for why this can't be a raw
    GFM-to-HTML pass-through."""
    if not markdown_body:
        return ""
    truncated = _truncate_markdown(markdown_body)
    rendered = _RENDER_MARKDOWN(truncated)
    if not isinstance(rendered, str):
        # Only possible if _RENDER_MARKDOWN were reconfigured with an AST
        # renderer instead of the HTML one it's built with below.
        msg = "mistune returned an AST instead of HTML — renderer misconfigured"
        raise RuntimeError(msg)
    return rendered.strip()


def _truncate_markdown(markdown_body: str) -> str:
    if len(markdown_body) <= _MAX_RELEASE_NOTES_MARKDOWN_CHARS:
        return markdown_body
    cut = markdown_body[:_MAX_RELEASE_NOTES_MARKDOWN_CHARS]
    last_newline = cut.rfind("\n")
    if last_newline > 0:
        cut = cut[:last_newline]
    return cut + _TRUNCATION_MARKER


class _TelegramHTMLRenderer(mistune.HTMLRenderer):
    """Overrides only the mistune renderer methods whose default output
    is a tag Telegram's HTML parser doesn't recognize."""

    def heading(self, *args: Any, **_kwargs: Any) -> str:
        # *args/**kwargs (matching image() below) rather than mistune's
        # own `(text, level, **attrs)` shape: ty's override check treats
        # a fully generic signature as compatible with any base
        # signature, whereas naming just `text` and catching the rest in
        # **attrs (still runtime-correct — render_token calls everything
        # by keyword) reads as a narrower, incompatible override.
        text = args[0]
        return "<b>" + text + "</b>\n\n"

    def paragraph(self, text: str) -> str:
        return text + "\n\n"

    def list(self, *args: Any, **_kwargs: Any) -> str:
        # Telegram has no list tag either way, ordered or not — bullets
        # for both is a deliberate simplification over hand-numbering.
        text = args[0]
        return text + "\n"

    def list_item(self, text: str) -> str:
        return "• " + text + "\n"

    def thematic_break(self) -> str:
        return ""

    def image(self, *_args: Any, **_kwargs: Any) -> str:
        return ""

    def linebreak(self) -> str:
        return "\n"

    def block_html(self, html: str) -> str:
        stripped = html.strip()
        return self.text(stripped) + "\n\n" if stripped else ""

    def block_error(self, text: str) -> str:
        return self.text(text) + "\n"


_RENDER_MARKDOWN = mistune.create_markdown(
    renderer=_TelegramHTMLRenderer(), plugins=["strikethrough"]
)
