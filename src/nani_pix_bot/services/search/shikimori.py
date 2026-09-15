"""Shikimori search — the Russian-community alternative to AniList,
called once per game at setup time, same as this package's anilist.py. See
MECHANICS.md's "Starting a game" section.

Talks to Shikimori's GraphQL API rather than its REST API (issue #104).
REST's detail endpoint (`/api/animes/:id`) returned `"english": [None]`
for a title with no recorded English name — a list *containing* a null,
not an absent field — which `parsing.optional_str_list` raised a
TypeError on, which `parsing.parse_entry` then turned into a silent skip
indistinguishable from a genuine 404 (issue #103): a starter picking a
valid search result got told "Couldn't find that anymore." GraphQL's
equivalent field, `Anime.english`, is a plain nullable `String` scalar —
that bug shape cannot occur here, so the fix is the migration itself
rather than a patch to the REST parsing.

Shikimori's `animes(search: ...)` query is deliberately light — no
`synonyms`/`english` — so a picked result is always re-fetched by id via
`animes(ids: ...)` for the full title/synonym set. This mirrors the
restart-resilient by-id re-fetch issue #11 already established for
AniList, except here it's forced by Shikimori's own schema shape rather
than a design choice.
"""

from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.search import cache, graphql, parsing

SHIKIMORI_GRAPHQL_URL = "https://shikimori.io/api/graphql"
SEARCH_RESULT_LIMIT = 5
# A fixed cap on how many screenshots are ever fetched/cached per anime
# — not a per-call parameter, so the cache key never needs to encode it
# (some titles have 30+ screenshots; the gallery UI (ticket 7) does its
# own client-side pagination/slicing of whatever this returns).
SCREENSHOT_FETCH_LIMIT = 20

# Shikimori asks API consumers to identify themselves with a descriptive
# User-Agent rather than a Referer (unlike AniList) — see the project's
# Shikimori research spike. Confirmed still required against the
# GraphQL endpoint, same as it was against REST.
_REQUEST_HEADERS = {"User-Agent": "nani-pix-bot (github.com/sm-steel/nani-pix-bot)"}

_API_NAME = Provider.SHIKIMORI.display_name

# This module's identity for graphql.py's shared request/error-handling
# plumbing (see graphql.py — anilist.py was its first caller; this is
# its second, issue #104).
_API = graphql.GraphQLApi(name=_API_NAME, url=SHIKIMORI_GRAPHQL_URL, headers=_REQUEST_HEADERS)

_SEARCH_QUERY = """
query ($search: String, $limit: Int) {
  animes(search: $search, limit: $limit) {
    id
    name
    russian
  }
}
"""

_DETAIL_QUERY = """
query ($ids: String) {
  animes(ids: $ids) {
    id
    name
    russian
    english
    synonyms
  }
}
"""

_SCREENSHOTS_QUERY = """
query ($ids: String) {
  animes(ids: $ids) {
    screenshots {
      originalUrl
    }
  }
}
"""


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
    """Search Shikimori anime titles matching `query`. The search query
    doesn't request synonyms/english — those are filled in by get_by_id
    once a result is picked. Cached briefly (see cache.py) so a starter
    repeating the same query doesn't re-hit the API each time."""
    variables = {"search": query, "limit": limit}
    data = await graphql.request(_API, client, query=_SEARCH_QUERY, variables=variables)
    # `animes` is a list directly — unlike AniList's `Page`, Shikimori's
    # schema has no wrapper object here, so there's no nested container
    # for graphql.require_object to validate; parse_entries below already
    # guards the list shape itself (and raises RuntimeError if it isn't
    # one), the same guard REST's `expect=list` used to provide.
    results = parsing.parse_entries(_API_NAME, data.get("animes"), _parse_search_result)
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
    setup-flow state issue #11 is actually about.

    Shikimori has no singular by-id query field — `animes(ids: ...)` is
    plural even for one id, confirmed live to answer an unknown id with
    an empty list (`{"data": {"animes": []}}`), HTTP 200, no `errors`.
    That's the actual not-found contract here, replacing REST's 404
    status this used to catch via `rest.fetch_by_id`."""
    variables = {"ids": str(shikimori_id)}
    data = await graphql.request(_API, client, query=_DETAIL_QUERY, variables=variables)
    animes = data.get("animes") or []
    if not animes:
        logger.debug("Shikimori id {} no longer found", shikimori_id)
        return None
    return parsing.parse_entry(_API_NAME, animes[0], _parse_detail_result)


@cache.cached()
async def screenshots(client: httpx.AsyncClient, shikimori_id: int) -> list[str]:
    """Real in-episode screenshots (not promotional art) for a
    Shikimori-identified anime — used by the screenshot-picker gallery.
    Cached (see cache.py) so repeatedly tapping "More screenshots" for
    the same anime re-slices the same cached list instead of re-hitting
    the API every time — pagination/slicing for display is the caller's
    job, not this function's."""
    variables = {"ids": str(shikimori_id)}
    data = await graphql.request(_API, client, query=_SCREENSHOTS_QUERY, variables=variables)
    animes = data.get("animes") or []
    entries = (animes[0].get("screenshots") or []) if animes else []
    # Through `parse_entries` rather than an inline comprehension:
    # `entry.get("originalUrl")` on a scalar is an AttributeError, and
    # this endpoint's entries are third-party data exactly like the
    # search endpoint's (issue #83).
    urls = parsing.parse_entries(_API_NAME, entries, _parse_screenshot_url)[:SCREENSHOT_FETCH_LIMIT]
    logger.debug("Shikimori id {} has {} screenshot(s) available", shikimori_id, len(entries))
    return urls


def _parse_shikimori_id(raw: dict) -> int:
    """`raw["id"]` as an int, from Shikimori's GraphQL `Anime.id`.

    Confirmed live: Shikimori's `id` is a GraphQL `ID` scalar, which this
    API serializes as a numeric *string* (`"id": "52991"`) in both
    `animes(search: ...)` and `animes(ids: ...)` responses — unlike
    AniList's `Media.id`, a genuine `Int!` that `parsing.require_int`
    (built for that shape) reads directly. Reusing `require_int` here
    unmodified would reject every single entry. This is a small
    Shikimori-specific sibling instead of a change to that shared
    helper, which stays exactly as-is for the providers whose ids really
    are JSON integers already.

    `ShikimoriResult.shikimori_id` stays an `int` regardless — unchanged
    public interface — so the string is converted here, at the one point
    that already validates every other field, rather than by a caller
    downstream that would have to re-learn this quirk. Anything that
    isn't a digit-only string (missing, null, a float, a bool, an array,
    an object, a non-numeric string) raises `TypeError`, which
    `parse_entry` turns into the same WARNING-and-skip as every other
    malformed id (issue #83)."""
    value = raw["id"]
    if not isinstance(value, str) or not value.isdigit():
        raise TypeError(f"'id' is {value!r}, expected a numeric string")
    return int(value)


def _parse_screenshot_url(raw: dict) -> str | None:
    """None for a screenshot with no usable URL — a decision, not a
    malformation, so `parse_entries` drops it without a warning (the
    same way jikan.py's `_picture_url` does). A URL that *is* there but
    isn't a string still warns (issue #86). Unlike REST's `original`
    field, GraphQL's `originalUrl` is confirmed live to already be
    absolute, so there's no more `SHIKIMORI_HOST` prefixing to do."""
    url = parsing.optional_str(raw, "originalUrl")
    return url or None


def _parse_search_result(raw: dict) -> ShikimoriResult:
    return ShikimoriResult(
        shikimori_id=_parse_shikimori_id(raw),
        title_romaji=parsing.optional_str(raw, "name"),
        title_english=None,
        title_russian=parsing.optional_str(raw, "russian"),
        synonyms=[],
    )


def _parse_detail_result(raw: dict) -> ShikimoriResult:
    """The only call that fills in `english`/`synonyms`, so it's where a
    malformed one actually reaches the game's answer key (issue #86).

    `english` no longer needs the list-indexing dance the REST version
    did: GraphQL's `Anime.english` is a plain nullable `String` scalar
    (the actual fix for issue #103, not just a defensive rewrite), so
    `parsing.optional_str` covers it exactly the way it covers `name`/
    `russian`."""
    return ShikimoriResult(
        shikimori_id=_parse_shikimori_id(raw),
        title_romaji=parsing.optional_str(raw, "name"),
        title_english=parsing.optional_str(raw, "english"),
        title_russian=parsing.optional_str(raw, "russian"),
        synonyms=parsing.optional_str_list(raw, "synonyms"),
    )
