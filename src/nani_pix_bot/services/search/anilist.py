"""AniList search — called once per game, at setup time only. See
MECHANICS.md's "Starting a game" and "Guess matching" sections: this
module is never consulted per guess, only to populate a Game's cached
title/synonyms.
"""

import sys
from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.search import cache, graphql, parsing
from nani_pix_bot.services.search.base import SearchModule

ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"
SEARCH_RESULT_LIMIT = 5

# The brand spelling this module identifies itself by in logs and in the
# `{service}` half of every message about it. A module constant because
# AniList talks GraphQL rather than going through `rest.RestApi` (which
# is where the other three keep the same string, as `RestApi.name`) — and
# because it is needed at three separate sites below, which is exactly
# how the same literal came to be written out three times.
_API_NAME = Provider.ANILIST.display_name

# AniList (via Cloudflare) 403s requests with no Referer, regardless of
# source IP/proxy — a browser-like Referer is enough, discovered against
# the real API after deploy.
_REQUEST_HEADERS = {"Referer": "https://anilist.co/"}

# This module's identity for graphql.py's shared request/error-handling
# plumbing (see graphql.py — anilist.py is its only caller today;
# shikimori.py's GraphQL migration, issue #104, will be its second).
_API = graphql.GraphQLApi(name=_API_NAME, url=ANILIST_GRAPHQL_URL, headers=_REQUEST_HEADERS)

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
    variables = {"search": query, "perPage": limit}
    data = await graphql.request(_API, client, query=_SEARCH_QUERY, variables=variables)
    page = graphql.require_object(_API, data.get("Page"), label="Page", variables=variables)
    results = parsing.parse_entries(_API_NAME, page.get("media") or [], _parse_result)
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
    data = await graphql.request(_API, client, query=_BY_ID_QUERY, variables={"id": anilist_id})
    media = data.get("Media")
    if media is None:
        logger.debug("AniList id {} no longer found", anilist_id)
        return None
    # `Media` sits in the same nested position `Page` does, but it is the
    # entry itself rather than a container of them, so it deliberately
    # does *not* go through `graphql.require_object`: an entry that
    # arrives but can't be parsed — scalar, missing `id`, whatever — lands
    # on the same None as a missing one, because there's nothing to stage
    # either way and "this pick is gone" is what the starter needs to
    # hear (#83).
    return parsing.parse_entry(_API_NAME, media, _parse_result)


# Provider.search_module's value for Provider.ANILIST — see
# services/search/base.py's module docstring.
service = SearchModule(sys.modules[__name__])


def _parse_result(raw: dict) -> AniListResult | None:
    """Every field is validated, not just the id: the guard around this
    function ends at the `return`, so a title that arrived as a number or
    a `synonyms` that arrived as a bare string used to sail out of here
    and detonate (or, for the string, quietly corrupt the answer key) at
    whatever consumed it — see `parsing.optional_str_list` (issue #86).

    `year` is the one field that takes `require_int` behind an `is None`
    pre-check rather than an `optional_*` helper, which is the split
    `require_int`'s docstring describes: it's optional here and nowhere
    else, so the pre-check says something at this one call site instead of
    being copy-pasted ahead of a dozen.

    Returns None (skipped quietly by `parse_entry`/`parse_entries`, same
    as any other "not wanted" decision) when every title variant is empty
    and there are no synonyms either — see `parsing.has_answer_key`
    (issue #89): a well-typed entry with no title and no synonyms stages
    a game `match_candidates()` can never match anything against."""
    anilist_id = parsing.require_int(raw, "id")
    title = raw["title"]
    title_romaji = parsing.optional_str(title, "romaji")
    title_english = parsing.optional_str(title, "english")
    title_native = parsing.optional_str(title, "native")
    synonyms = parsing.optional_str_list(raw, "synonyms")
    if not parsing.has_answer_key((title_romaji, title_english, title_native), synonyms):
        logger.debug(
            "AniList id {} has no title in any variant and no synonyms, skipping", anilist_id
        )
        return None
    start_date = raw.get("startDate") or {}
    year = None if start_date.get("year") is None else parsing.require_int(start_date, "year")
    return AniListResult(
        anilist_id=anilist_id,
        title_romaji=title_romaji,
        title_english=title_english,
        title_native=title_native,
        synonyms=synonyms,
        year=year,
    )
