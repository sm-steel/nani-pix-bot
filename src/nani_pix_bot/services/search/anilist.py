"""AniList search — called once per game, at setup time only. See
MECHANICS.md's "Starting a game" and "Guess matching" sections: this
module is never consulted per guess, only to populate a Game's cached
title/synonyms.
"""

from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.services.search import cache, http_retry

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


@cache.cached()
async def search(
    client: httpx.AsyncClient, query: str, *, limit: int = SEARCH_RESULT_LIMIT
) -> list[AniListResult]:
    """Search AniList anime titles matching `query`. Cached briefly (see
    cache.py) so a starter repeating the same query doesn't re-hit the
    API each time."""
    data = await _request(
        client, query=_SEARCH_QUERY, variables={"search": query, "perPage": limit}
    )
    page = data.get("Page") or {}
    results = [_parse_result(raw) for raw in page.get("media") or []]
    logger.debug("AniList search {!r} returned {} result(s)", query, len(results))
    return results


@cache.cached()
async def get_by_id(client: httpx.AsyncClient, anilist_id: int) -> AniListResult | None:
    """Re-fetch a single anime by id — used when the starter taps an
    AniList-picker button. Cached briefly (see cache.py) — a
    short-lived, in-process-only performance optimization, distinct
    from issue #11's restart-resilience point (that's about the
    anilist_id itself surviving in the button's callback_data across a
    restart, not about this in-process cache, which is wiped on every
    restart same as everything else in it)."""
    data = await _request(client, query=_BY_ID_QUERY, variables={"id": anilist_id})
    media = data.get("Media")
    if media is None:
        logger.debug("AniList id {} no longer found", anilist_id)
        return None
    return _parse_result(media)


async def _request(client: httpx.AsyncClient, *, query: str, variables: dict) -> dict:
    """POST the GraphQL document and hand back its `data` object.

    AniList sits behind Cloudflare, which answers with an HTML
    interstitial often enough that a 200 is no guarantee of a JSON body.
    That becomes a `RuntimeError` — in the handlers'
    `_SEARCH_SERVICE_ERRORS` tuple — rather than the `ValueError`
    `response.json()` raises, which nothing catches (issue #75). A
    GraphQL error response (a null `data` alongside `errors`) yields an
    empty object, so the callers below find no media rather than raising."""

    async def make_request() -> httpx.Response:
        return await client.post(
            ANILIST_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=_REQUEST_HEADERS,
        )

    response = await http_retry.request_with_retry(
        make_request, service_name="AniList", context=f"variables {variables!r}"
    )
    try:
        body = response.json()
    except ValueError as exc:
        msg = f"AniList returned a non-JSON body for variables {variables!r}"
        logger.error(msg)
        raise RuntimeError(msg) from exc
    return body.get("data") or {}


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
