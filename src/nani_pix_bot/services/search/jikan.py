"""Jikan search — the unofficial MyAnimeList REST API. Called once per
game, at setup time only, same as this package's anilist.py/
shikimori.py. See MECHANICS.md's "Starting a game" section.

Jikan's endpoints already return the richer title/synonym fields on
both the search (list) and detail (by-id) response, unlike Shikimori's
list endpoint — so both search() and get_by_id() parse the same shape
here, no forced re-fetch-by-id needed for the full field set.
"""

from dataclasses import dataclass
from http import HTTPStatus

import httpx
from loguru import logger

from nani_pix_bot.services.search import cache, http_retry

JIKAN_BASE_URL = "https://api.jikan.moe/v4/anime"
SEARCH_RESULT_LIMIT = 5
# A fixed cap on how many pictures are ever fetched/cached per anime —
# not a per-call parameter, so the cache key never needs to encode it
# (the gallery UI, ticket 7, does its own client-side pagination of
# whatever this returns).
SCREENSHOT_FETCH_LIMIT = 20

# Jikan doesn't require an API key, but — same courtesy as
# shikimori.py's User-Agent — identifies this bot as a consumer of a
# shared, community-run resource rather than an anonymous default.
_REQUEST_HEADERS = {"User-Agent": "nani-pix-bot (github.com/sm-steel/nani-pix-bot)"}


@dataclass(frozen=True)
class JikanResult:
    jikan_id: int
    title_romaji: str | None
    title_english: str | None
    title_native: str | None
    synonyms: list[str]


async def search(
    client: httpx.AsyncClient, query: str, *, limit: int = SEARCH_RESULT_LIMIT
) -> list[JikanResult]:
    """Search Jikan anime titles matching `query`. Cached briefly (see
    cache.py) so a starter repeating the same query doesn't re-hit the
    API each time."""
    return await cache.cached(
        client, "jikan.search", query, limit, fetch=lambda: _search(client, query, limit)
    )


async def _search(client: httpx.AsyncClient, query: str, limit: int) -> list[JikanResult]:
    params = {"q": query, "limit": limit}
    data = await _request(client, url=JIKAN_BASE_URL, params=params)
    results = [_parse_result(raw) for raw in data["data"]]
    logger.debug("Jikan search {!r} returned {} result(s)", query, len(results))
    return results


async def get_by_id(client: httpx.AsyncClient, jikan_id: int) -> JikanResult | None:
    """Re-fetch a single anime by id — used when the starter taps a
    Jikan-picker button. Cached briefly (see cache.py) — a short-lived,
    in-process-only performance optimization, not a substitute for the
    restart-resilient by-id re-fetch pattern issue #11 established
    (that's about not caching in ephemeral bot memory across a
    restart; this cache is wiped on every restart same as everything
    else in it)."""
    return await cache.cached(
        client, "jikan.get_by_id", jikan_id, fetch=lambda: _get_by_id(client, jikan_id)
    )


async def _get_by_id(client: httpx.AsyncClient, jikan_id: int) -> JikanResult | None:
    try:
        entry = await _request(client, url=f"{JIKAN_BASE_URL}/{jikan_id}", params={})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == HTTPStatus.NOT_FOUND:
            logger.debug("Jikan id {} no longer found", jikan_id)
            return None
        raise
    return _parse_result(entry["data"])


async def screenshots(client: httpx.AsyncClient, jikan_id: int) -> list[str]:
    """Promotional/episode pictures for a Jikan-identified anime — not
    true in-episode frame grabs the way Shikimori's are, but usable
    (see the "start a game without a screenshot" plan's research).
    Cached (see cache.py) so repeatedly tapping "More screenshots" for
    the same anime re-slices the same cached list instead of re-hitting
    the API every time."""
    return await cache.cached(
        client, "jikan.screenshots", jikan_id, fetch=lambda: _screenshots(client, jikan_id)
    )


async def _screenshots(client: httpx.AsyncClient, jikan_id: int) -> list[str]:
    data = await _request(client, url=f"{JIKAN_BASE_URL}/{jikan_id}/pictures", params={})
    entries = data["data"][:SCREENSHOT_FETCH_LIMIT]
    urls = [url for entry in entries if (url := _picture_url(entry)) is not None]
    logger.debug("Jikan id {} has {} picture(s) available", jikan_id, len(data["data"]))
    return urls


def _picture_url(entry: dict) -> str | None:
    jpg = entry.get("jpg") or {}
    return jpg.get("large_image_url") or jpg.get("image_url")


async def _request(client: httpx.AsyncClient, *, url: str, params: dict) -> dict:
    async def make_request() -> httpx.Response:
        return await client.get(url, params=params, headers=_REQUEST_HEADERS)

    response = await http_retry.request_with_retry(
        make_request, service_name="Jikan", context=f"url {url!r}, params {params!r}"
    )
    return response.json()


def _parse_result(raw: dict) -> JikanResult:
    return JikanResult(
        jikan_id=raw["mal_id"],
        title_romaji=raw.get("title"),
        title_english=raw.get("title_english"),
        title_native=raw.get("title_japanese"),
        synonyms=raw.get("title_synonyms") or [],
    )
