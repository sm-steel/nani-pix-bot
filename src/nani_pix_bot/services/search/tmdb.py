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

from nani_pix_bot.services.search import http_retry

TMDB_BASE_URL = "https://api.themoviedb.org/3"
SEARCH_RESULT_LIMIT = 5


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
    applied client-side."""
    data = await _request(client, url=f"{TMDB_BASE_URL}/search/tv", params={"query": query})
    results = [_parse_result(raw) for raw in data["results"][:limit]]
    logger.debug("TMDB search {!r} returned {} result(s)", query, len(results))
    return results


async def get_by_id(client: httpx.AsyncClient, tmdb_id: int) -> TMDBResult | None:
    """Re-fetch a single show by id — used when the starter taps a
    TMDB-picker button, rather than caching search results in ephemeral
    bot memory (see issue #11's restart-resilient precedent, already
    established for AniList/Shikimori/Jikan)."""
    try:
        entry = await _request(client, url=f"{TMDB_BASE_URL}/tv/{tmdb_id}", params={})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == HTTPStatus.NOT_FOUND:
            logger.debug("TMDB id {} no longer found", tmdb_id)
            return None
        raise
    return _parse_result(entry)


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
