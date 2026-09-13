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

from nani_pix_bot.services.search import http_retry

JIKAN_BASE_URL = "https://api.jikan.moe/v4/anime"
SEARCH_RESULT_LIMIT = 5

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
    """Search Jikan anime titles matching `query`."""
    params = {"q": query, "limit": limit}
    data = await _request(client, url=JIKAN_BASE_URL, params=params)
    results = [_parse_result(raw) for raw in data["data"]]
    logger.debug("Jikan search {!r} returned {} result(s)", query, len(results))
    return results


async def get_by_id(client: httpx.AsyncClient, jikan_id: int) -> JikanResult | None:
    """Re-fetch a single anime by id — used when the starter taps a
    Jikan-picker button, rather than caching search results in
    ephemeral bot memory (see issue #11's restart-resilient precedent,
    already established for AniList/Shikimori)."""
    try:
        entry = await _request(client, url=f"{JIKAN_BASE_URL}/{jikan_id}", params={})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == HTTPStatus.NOT_FOUND:
            logger.debug("Jikan id {} no longer found", jikan_id)
            return None
        raise
    return _parse_result(entry["data"])


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
