"""TMDB search — anime are modeled as regular TV shows in TMDB's own
schema, so this hits `/search/tv`/`/tv/{id}` rather than a
movie/anime-specific endpoint. Called once per game, at setup time
only, same as this package's anilist.py/shikimori.py/jikan.py.

Unlike every other provider in this package, TMDB requires an API key
(a v4 "Read Access Token", Bearer-auth) and is DNS-blocked directly
from `moscow` — both handled by the caller's client construction
(`app.py` builds a dedicated proxied client with the token already set
as a default `Authorization` header), not by this module. See
ARCHITECTURE.md's connectivity section.

No synonyms/romaji field: TMDB has no distinct "romaji title" concept,
and fetching alternative titles would need a second API call per
result — out of scope for now (see the "start a game without a
screenshot" plan). `title_romaji`/`synonyms` are always None/`[]`,
kept on `TMDBResult` only so it has the same shape `stage_result()`
already expects from every other provider's result dataclass.
"""

from dataclasses import dataclass
from http import HTTPStatus

import httpx
from loguru import logger

from nani_pix_bot.services.search import cache, http_retry

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/original"
SEARCH_RESULT_LIMIT = 5
# A fixed cap on how many episode stills are ever fetched/cached per
# show — not a per-call parameter, so the cache key never needs to
# encode it (the gallery UI, ticket 7, does its own client-side
# pagination of whatever this returns). Also bounds how many extra
# per-episode API calls screenshots() makes (see its docstring).
SCREENSHOT_FETCH_LIMIT = 20


@dataclass(frozen=True)
class TMDBResult:
    tmdb_id: int
    title_romaji: str | None
    title_english: str | None
    title_native: str | None
    synonyms: list[str]


async def search(
    client: httpx.AsyncClient, query: str, *, limit: int = SEARCH_RESULT_LIMIT
) -> list[TMDBResult]:
    """Search TMDB TV shows matching `query`. TMDB paginates at a fixed
    20-per-page rather than taking a result-count param, so `limit` is
    applied client-side. Cached briefly (see cache.py) so a starter
    repeating the same query doesn't re-hit the API each time."""
    return await cache.cached(
        client, "tmdb.search", query, limit, fetch=lambda: _search(client, query, limit)
    )


async def _search(client: httpx.AsyncClient, query: str, limit: int) -> list[TMDBResult]:
    data = await _request(client, url=f"{TMDB_BASE_URL}/search/tv", params={"query": query})
    results = [_parse_result(raw) for raw in data["results"][:limit]]
    logger.debug("TMDB search {!r} returned {} result(s)", query, len(results))
    return results


async def get_by_id(client: httpx.AsyncClient, tmdb_id: int) -> TMDBResult | None:
    """Re-fetch a single show by id — used when the starter taps a
    TMDB-picker button. Cached briefly (see cache.py) — a short-lived,
    in-process-only performance optimization, not a substitute for the
    restart-resilient by-id re-fetch pattern issue #11 established."""
    return await cache.cached(
        client, "tmdb.get_by_id", tmdb_id, fetch=lambda: _get_by_id(client, tmdb_id)
    )


async def _get_by_id(client: httpx.AsyncClient, tmdb_id: int) -> TMDBResult | None:
    try:
        entry = await _request(client, url=f"{TMDB_BASE_URL}/tv/{tmdb_id}", params={})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == HTTPStatus.NOT_FOUND:
            logger.debug("TMDB id {} no longer found", tmdb_id)
            return None
        raise
    return _parse_result(entry)


async def screenshots(client: httpx.AsyncClient, tmdb_id: int) -> list[str]:
    """Real per-episode stills (not promotional art) for a
    TMDB-identified show — used by the screenshot-picker gallery.
    Unlike Shikimori/Jikan's single-call screenshot endpoints, TMDB has
    no bulk "all stills for this show" resource: the per-episode detail
    endpoint (`/tv/{id}/season/{s}/episode/{e}`) already includes a
    `still_path` directly, so this fetches the show's first season's
    episode list once, then one extra call per episode (up to
    SCREENSHOT_FETCH_LIMIT) — more chatty than the other two providers,
    but each response is cached individually (see cache.py) so
    repeating this for the same show is still just one round-trip
    total after the first fetch."""
    return await cache.cached(
        client, "tmdb.screenshots", tmdb_id, fetch=lambda: _screenshots(client, tmdb_id)
    )


async def _screenshots(client: httpx.AsyncClient, tmdb_id: int) -> list[str]:
    show = await _request(client, url=f"{TMDB_BASE_URL}/tv/{tmdb_id}", params={})
    seasons = [s for s in show.get("seasons", []) if s.get("season_number", 0) >= 1]
    if not seasons:
        logger.debug("TMDB id {} has no real seasons to pull stills from", tmdb_id)
        return []

    season_number = seasons[0]["season_number"]
    episode_count = seasons[0].get("episode_count", 0)
    episode_numbers = range(1, min(episode_count, SCREENSHOT_FETCH_LIMIT) + 1)

    urls = []
    for episode_number in episode_numbers:
        episode = await _request(
            client,
            url=f"{TMDB_BASE_URL}/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}",
            params={},
        )
        still_path = episode.get("still_path")
        if still_path:
            urls.append(f"{TMDB_IMAGE_BASE_URL}{still_path}")
    logger.debug(
        "TMDB id {} yielded {} still(s) from {} episode(s)",
        tmdb_id,
        len(urls),
        len(episode_numbers),
    )
    return urls


async def _request(client: httpx.AsyncClient, *, url: str, params: dict) -> dict:
    async def make_request() -> httpx.Response:
        return await client.get(url, params=params)

    response = await http_retry.request_with_retry(
        make_request, service_name="TMDB", context=f"url {url!r}, params {params!r}"
    )
    return response.json()


def _parse_result(raw: dict) -> TMDBResult:
    return TMDBResult(
        tmdb_id=raw["id"],
        title_romaji=None,
        title_english=raw.get("name"),
        title_native=raw.get("original_name"),
        synonyms=[],
    )
