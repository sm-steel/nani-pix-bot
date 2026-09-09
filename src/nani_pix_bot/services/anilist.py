"""AniList search — called once per game, at setup time only. See
MECHANICS.md's "Starting a game" and "Guess matching" sections: this
module is never consulted per guess, only to populate a Game's cached
title/synonyms.
"""

import asyncio
from dataclasses import dataclass
from http import HTTPStatus

import httpx
from loguru import logger

ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"
MAX_RATE_LIMIT_RETRIES = 5
DEFAULT_RETRY_AFTER_SECONDS = 5.0
SEARCH_RESULT_LIMIT = 5

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
    page = await _request(client, query=query, per_page=limit)
    return [_parse_result(raw) for raw in page["media"]]


async def _request(client: httpx.AsyncClient, *, query: str, per_page: int) -> dict:
    for _attempt in range(MAX_RATE_LIMIT_RETRIES):
        response = await client.post(
            ANILIST_GRAPHQL_URL,
            json={"query": _SEARCH_QUERY, "variables": {"search": query, "perPage": per_page}},
        )
        if response.status_code != HTTPStatus.TOO_MANY_REQUESTS:
            response.raise_for_status()
            return response.json()["data"]["Page"]
        retry_after = float(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS))
        logger.warning("AniList rate-limited search {!r}, retrying in {}s", query, retry_after)
        await asyncio.sleep(retry_after)
    msg = f"AniList rate limit retries exhausted (search {query!r})"
    logger.error(msg)
    raise RuntimeError(msg)


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
