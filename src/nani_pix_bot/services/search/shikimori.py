"""Shikimori search — the Russian-community alternative to AniList,
called once per game at setup time, same as this package's anilist.py. See
MECHANICS.md's "Starting a game" section.

Shikimori's list endpoint (`/api/animes`) is deliberately light — no
`synonyms`/`english` — so a picked result is always re-fetched by id via
the detail endpoint (`/api/animes/:id`) for the full title/synonym set.
This mirrors the restart-resilient by-id re-fetch issue #11 already
established for AniList, except here it's forced by Shikimori's own API
shape rather than a design choice.
"""

from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.search import cache, parsing, rest

# Shikimori's older shikimori.one domain now permanently 301-redirects
# here — and shikimori.one is itself unreachable directly from moscow,
# while this .io domain is (our httpx client doesn't follow redirects,
# so pointing at the old domain would just return an HTML redirect page
# instead of JSON). See ARCHITECTURE.md's "AniList/Shikimori connectivity".
# Screenshot image paths returned by the API are relative to this same
# host too (confirmed live — shikimori.one, the historical image host,
# is unreachable direct from moscow just like the API itself).
SHIKIMORI_HOST = "https://shikimori.io"
SHIKIMORI_BASE_URL = f"{SHIKIMORI_HOST}/api/animes"
SEARCH_RESULT_LIMIT = 5
# A fixed cap on how many screenshots are ever fetched/cached per anime
# — not a per-call parameter, so the cache key never needs to encode it
# (some titles have 30+ screenshots; the gallery UI (ticket 7) does its
# own client-side pagination/slicing of whatever this returns).
SCREENSHOT_FETCH_LIMIT = 20

# Shikimori asks API consumers to identify themselves with a descriptive
# User-Agent rather than a Referer (unlike AniList) — see the project's
# Shikimori research spike.
_REQUEST_HEADERS = {"User-Agent": "nani-pix-bot (github.com/sm-steel/nani-pix-bot)"}

_API = rest.RestApi(name=Provider.SHIKIMORI.display_name, headers=_REQUEST_HEADERS)


@dataclass(frozen=True)
class ShikimoriResult:
    shikimori_id: int
    title_romaji: str | None
    title_english: str | None
    title_russian: str | None
    synonyms: list[str]


@cache.cached()
async def search(
    client: httpx.AsyncClient, query: str, *, limit: int = SEARCH_RESULT_LIMIT
) -> list[ShikimoriResult]:
    """Search Shikimori anime titles matching `query`. The list endpoint
    doesn't return synonyms/english — those are filled in by get_by_id
    once a result is picked. Cached briefly (see cache.py) so a starter
    repeating the same query doesn't re-hit the API each time."""
    params = {"search": query, "limit": limit}
    entries = await rest.get_json(_API, client, SHIKIMORI_BASE_URL, params, expect=list)
    results = parsing.parse_entries(_API.name, entries, _parse_search_result)
    logger.debug("Shikimori search {!r} returned {} result(s)", query, len(results))
    return results


@cache.cached()
async def get_by_id(client: httpx.AsyncClient, shikimori_id: int) -> ShikimoriResult | None:
    """Re-fetch a single anime by id — used when the starter taps a
    Shikimori-picker button. See module docstring: this is the only call
    that returns synonyms/english. Not a restart-resilience concern
    (issue #11) to cache this briefly — the cache (see cache.py) is a
    short-lived, in-process-only performance optimization, wiped on
    every restart same as everything else in it, unlike the DB-derived
    setup-flow state issue #11 is actually about."""
    return await rest.fetch_by_id(
        _API, client, f"{SHIKIMORI_BASE_URL}/{shikimori_id}", shikimori_id, _parse_detail_result
    )


@cache.cached()
async def screenshots(client: httpx.AsyncClient, shikimori_id: int) -> list[str]:
    """Real in-episode screenshots (not promotional art) for a
    Shikimori-identified anime — used by the screenshot-picker gallery.
    Cached (see cache.py) so repeatedly tapping "More screenshots" for
    the same anime re-slices the same cached list instead of re-hitting
    the API every time — pagination/slicing for display is the caller's
    job, not this function's."""
    entries = await rest.get_json(
        _API, client, f"{SHIKIMORI_BASE_URL}/{shikimori_id}/screenshots", {}, expect=list
    )
    # Through `parse_entries` rather than the inline comprehension this
    # used to be: `entry.get("original")` on a scalar is an
    # AttributeError, and this endpoint's entries are third-party data
    # exactly like the search endpoint's (issue #83).
    urls = parsing.parse_entries(_API.name, entries, _parse_screenshot_url)[:SCREENSHOT_FETCH_LIMIT]
    logger.debug("Shikimori id {} has {} screenshot(s) available", shikimori_id, len(entries))
    return urls


def _parse_screenshot_url(raw: dict) -> str | None:
    """None for a screenshot with no usable path — a decision, not a
    malformation, so `parse_entries` drops it without a warning (the
    same way jikan.py's `_picture_url` does). A path that *is* there but
    isn't a string is the other thing entirely, and warns: the f-string
    below interpolates anything, so `{"original": {"a": 1}}` used to
    produce the URL `https://shikimori.io{'a': 1}` and only fail later,
    at `InputMediaPhoto(media=url)` (issue #86)."""
    path = parsing.optional_str(raw, "original")
    return f"{SHIKIMORI_HOST}{path}" if path else None


def _parse_search_result(raw: dict) -> ShikimoriResult:
    return ShikimoriResult(
        shikimori_id=parsing.require_int(raw, "id"),
        title_romaji=parsing.optional_str(raw, "name"),
        title_english=None,
        title_russian=parsing.optional_str(raw, "russian"),
        synonyms=[],
    )


def _parse_detail_result(raw: dict) -> ShikimoriResult:
    """The only call that fills in `english`/`synonyms`, so it's where a
    malformed one actually reaches the game's answer key (issue #86).

    `english[0]` looked like it was already guarded, and half of it was:
    indexing a number raises `TypeError` and indexing an object raises
    `KeyError`, both inside `parse_entry`. The half it missed is the half
    that matters — `"Frieren"[0]` is `"F"`, so a string `english` staged a
    game titled "F" without raising anything, and `[5][0]` handed back a
    non-string title that detonated later in the preview's `", ".join`.
    Coverage by accident, in other words, and only against the shapes
    nobody minds losing. `optional_str_list` covers all four shapes on
    purpose, and keeps covering them if this read is ever rewritten."""
    english = parsing.optional_str_list(raw, "english")
    return ShikimoriResult(
        shikimori_id=parsing.require_int(raw, "id"),
        title_romaji=parsing.optional_str(raw, "name"),
        title_english=english[0] if english else None,
        title_russian=parsing.optional_str(raw, "russian"),
        synonyms=parsing.optional_str_list(raw, "synonyms"),
    )
