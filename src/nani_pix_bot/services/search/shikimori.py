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
from http import HTTPStatus

import httpx
from loguru import logger

from nani_pix_bot.services.search import cache, http_retry

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
    entries = await _request(client, url=SHIKIMORI_BASE_URL, params=params)
    results = [_parse_search_result(entry) for entry in entries]
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
    try:
        entry = await _request(client, url=f"{SHIKIMORI_BASE_URL}/{shikimori_id}", params={})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == HTTPStatus.NOT_FOUND:
            logger.debug("Shikimori id {} no longer found", shikimori_id)
            return None
        raise
    return _parse_detail_result(entry)


@cache.cached()
async def screenshots(client: httpx.AsyncClient, shikimori_id: int) -> list[str]:
    """Real in-episode screenshots (not promotional art) for a
    Shikimori-identified anime — used by the screenshot-picker gallery.
    Cached (see cache.py) so repeatedly tapping "More screenshots" for
    the same anime re-slices the same cached list instead of re-hitting
    the API every time — pagination/slicing for display is the caller's
    job, not this function's."""
    entries = await _request(
        client, url=f"{SHIKIMORI_BASE_URL}/{shikimori_id}/screenshots", params={}
    )
    urls = [f"{SHIKIMORI_HOST}{entry['original']}" for entry in entries[:SCREENSHOT_FETCH_LIMIT]]
    logger.debug("Shikimori id {} has {} screenshot(s) available", shikimori_id, len(entries))
    return urls


async def _request(client: httpx.AsyncClient, *, url: str, params: dict) -> dict:
    async def make_request() -> httpx.Response:
        return await client.get(url, params=params, headers=_REQUEST_HEADERS)

    response = await http_retry.request_with_retry(
        make_request, service_name="Shikimori", context=f"url {url!r}, params {params!r}"
    )
    return response.json()


def _parse_search_result(raw: dict) -> ShikimoriResult:
    return ShikimoriResult(
        shikimori_id=raw["id"],
        title_romaji=raw.get("name"),
        title_english=None,
        title_russian=raw.get("russian"),
        synonyms=[],
    )


def _parse_detail_result(raw: dict) -> ShikimoriResult:
    english = raw.get("english") or []
    return ShikimoriResult(
        shikimori_id=raw["id"],
        title_romaji=raw.get("name"),
        title_english=english[0] if english else None,
        title_russian=raw.get("russian"),
        synonyms=raw.get("synonyms") or [],
    )
