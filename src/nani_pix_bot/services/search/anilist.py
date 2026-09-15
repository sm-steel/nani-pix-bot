"""AniList search — called once per game, at setup time only. See
MECHANICS.md's "Starting a game" and "Guess matching" sections: this
module is never consulted per guess, only to populate a Game's cached
title/synonyms.
"""

from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.search import cache, http_retry, parsing

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
    data = await _request(client, query=_SEARCH_QUERY, variables=variables)
    page = _require_object(data.get("Page"), label="Page", variables=variables)
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
    data = await _request(client, query=_BY_ID_QUERY, variables={"id": anilist_id})
    media = data.get("Media")
    if media is None:
        logger.debug("AniList id {} no longer found", anilist_id)
        return None
    # `Media` sits in the same nested position `Page` does, but it is the
    # entry itself rather than a container of them, so it deliberately
    # does *not* go through `_require_object`: an entry that arrives but
    # can't be parsed — scalar, missing `id`, whatever — lands on the same
    # None as a missing one, because there's nothing to stage either way
    # and "this pick is gone" is what the starter needs to hear (#83).
    return parsing.parse_entry(_API_NAME, media, _parse_result)


async def _request(client: httpx.AsyncClient, *, query: str, variables: dict) -> dict:
    """POST the GraphQL document and hand back its `data` object.

    AniList sits behind Cloudflare, which answers with an HTML
    interstitial often enough that a 200 is no guarantee of a JSON
    object. A body that doesn't decode, and one that decodes to anything
    other than an object (`null` decodes perfectly well and would reach
    the callers below as an `AttributeError`), both become a
    `RuntimeError` — which is in the handlers' `_SEARCH_SERVICE_ERRORS`
    tuple, unlike what they'd otherwise raise (issue #75).

    GraphQL reports failures in an `errors` array rather than in the
    status code, so those are never dropped silently: they're always
    logged at ERROR, and when nothing usable came back with them they
    raise too. Falling back to an empty object there would have told the
    starter "no results found" / "your pick is gone" — both flat lies
    about a search that never ran. A null `data` with no `errors`
    alongside it is just a missing container key, and does yield an empty
    object.

    The `data` object itself gets the same treatment as the body around
    it, which is what makes the `-> dict` above true rather than
    aspirational: `return data or {}` handed a `{"data": 5}` body's
    scalar straight to the callers, where it detonated on their first
    `.get` — before the container they were actually reading was ever
    looked at (issue #85)."""

    async def make_request() -> httpx.Response:
        return await client.post(
            ANILIST_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=_REQUEST_HEADERS,
        )

    response = await http_retry.request_with_retry(
        make_request, service_name=_API_NAME, context=f"variables {variables!r}"
    )
    try:
        body = response.json()
    except ValueError as exc:
        msg = f"AniList returned a non-JSON body for variables {variables!r}"
        logger.error(msg)
        raise RuntimeError(msg) from exc
    if not isinstance(body, dict):
        msg = (
            f"AniList answered 200 with a {type(body).__name__} body "
            f"for variables {variables!r}, expected an object"
        )
        logger.error(msg)
        raise RuntimeError(msg)

    data = body.get("data")
    if errors := body.get("errors"):
        msg = f"AniList reported GraphQL error(s) for variables {variables!r}: {errors!r}"
        logger.error(msg)
        if not data:
            raise RuntimeError(msg)
    return _require_object(data, label="data", variables=variables)


def _require_object(value: Any, *, label: str, variables: dict) -> dict:
    """`value` as a JSON object, `{}` when it simply isn't there, and a
    `RuntimeError` when it is there but isn't one.

    AniList is the only provider reading a container *nested inside* the
    body, so `rest.get_json`'s `expect=` — which the other three lean on —
    never covers it: GraphQL has no by-id URL and no 404 semantics, so
    `anilist.py` shares none of that plumbing (see `rest.py`'s docstring),
    and that asymmetry is exactly where issue #85's leak lived. Both
    nested objects this module reads, `data` and the `Page` inside it,
    come back through here, so no caller is left holding an unvalidated
    container: `parsing.py`'s rule is that a guard's boundary is the
    return, and a `dict` return is the boundary being honoured.

    Absent and malformed stay two different answers, the same split
    `require_int` draws: a null container key is an empty result the
    pickers already render as "nothing found", while a number where an
    object belongs is a provider we can't read at all — `RuntimeError`,
    which is in `_SEARCH_SERVICE_ERRORS`, unlike the `AttributeError`
    the unguarded `.get` used to raise."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = (
            f"AniList answered 200 with a {type(value).__name__} in {label!r} "
            f"for variables {variables!r}, expected an object"
        )
        logger.error(msg)
        raise RuntimeError(msg)
    return value


def _parse_result(raw: dict) -> AniListResult:
    """Every field is validated, not just the id: the guard around this
    function ends at the `return`, so a title that arrived as a number or
    a `synonyms` that arrived as a bare string used to sail out of here
    and detonate (or, for the string, quietly corrupt the answer key) at
    whatever consumed it — see `parsing.optional_str_list` (issue #86).

    `year` is the one field that takes `require_int` behind an `is None`
    pre-check rather than an `optional_*` helper, which is the split
    `require_int`'s docstring describes: it's optional here and nowhere
    else, so the pre-check says something at this one call site instead of
    being copy-pasted ahead of a dozen."""
    title = raw["title"]
    start_date = raw.get("startDate") or {}
    year = None if start_date.get("year") is None else parsing.require_int(start_date, "year")
    return AniListResult(
        anilist_id=parsing.require_int(raw, "id"),
        title_romaji=parsing.optional_str(title, "romaji"),
        title_english=parsing.optional_str(title, "english"),
        title_native=parsing.optional_str(title, "native"),
        synonyms=parsing.optional_str_list(raw, "synonyms"),
        year=year,
    )
