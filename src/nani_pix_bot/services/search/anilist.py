"""AniList search — called once per game, at setup time only. See
MECHANICS.md's "Starting a game" and "Guess matching" sections: this
module is never consulted per guess, only to populate a Game's cached
title/synonyms.
"""

from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.services.search import http_retry

ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"
SEARCH_RESULT_LIMIT = 5

# AniList (via Cloudflare) 403s requests with no Referer, regardless of
# source IP/proxy — a browser-like Referer is enough, discovered against
# the real API after deploy.
_REQUEST_HEADERS = {"Referer": "https://anilist.co/"}

_SEARCH_QUERY = """
query ($search: String, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: ANIME) {
      id
      title { romaji english native }
      synonyms
      startDate { year }
    }
  }
}
"""

_BY_ID_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    title { romaji english native }
    synonyms
    startDate { year }
  }
}
"""


@dataclass(frozen=True)
class AniListResult:
    anilist_id: int
    title_romaji: str | None
    title_english: str | None
    title_native: str | None
    synonyms: list[str]
    year: int | None


async def search(
    client: httpx.AsyncClient, query: str, *, limit: int = SEARCH_RESULT_LIMIT
) -> list[AniListResult]:
    """Search AniList anime titles matching `query`."""
    data = await _request(
        client, query=_SEARCH_QUERY, variables={"search": query, "perPage": limit}
    )
    results = [_parse_result(raw) for raw in data["Page"]["media"]]
    logger.debug("AniList search {!r} returned {} result(s)", query, len(results))
    return results


async def get_by_id(client: httpx.AsyncClient, anilist_id: int) -> AniListResult | None:
    """Re-fetch a single anime by id — used when the starter taps an
    AniList-picker button, rather than caching search results in
    ephemeral bot memory (see issue #11: that cache doesn't survive a
    restart, but the anilist_id embedded in the button's callback_data,
    stored by Telegram on the message itself, does)."""
    data = await _request(client, query=_BY_ID_QUERY, variables={"id": anilist_id})
    media = data["Media"]
    if media is None:
        logger.debug("AniList id {} no longer found", anilist_id)
        return None
    return _parse_result(media)


async def _request(client: httpx.AsyncClient, *, query: str, variables: dict) -> dict:
    async def make_request() -> httpx.Response:
        return await client.post(
            ANILIST_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=_REQUEST_HEADERS,
        )

    response = await http_retry.request_with_retry(
        make_request, service_name="AniList", context=f"variables {variables!r}"
    )
    return response.json()["data"]


def _parse_result(raw: dict) -> AniListResult:
    title = raw["title"]
    return AniListResult(
        anilist_id=raw["id"],
        title_romaji=title.get("romaji"),
        title_english=title.get("english"),
        title_native=title.get("native"),
        synonyms=raw.get("synonyms") or [],
        year=(raw.get("startDate") or {}).get("year"),
    )
