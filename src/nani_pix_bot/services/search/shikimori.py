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

`synonyms`' item-level nullability (the same null-in-a-list shape
REST's `english` bug had) couldn't be confirmed via GraphQL
introspection — the live API caps query depth at 5, one hop short of
inspecting a list field's inner item type. Empirically checked instead:
53 real anime entries sampled across a wide id range (including 817 and
1790, the original issue #103 repro titles) via live queries against
`shikimori.io/api/graphql`. Every `synonyms` value observed was either
`[]` or a list of real strings, never a list containing a null. No code
change was needed either way — `parsing.optional_str_list` already
raises (and `parse_entry` already skips) on a null member, the same as
for every other provider's list fields — this note just records that
the shape was actually checked, not assumed.
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
# A floor on Shikimori's own score field, applied only to the random
# pick (services/game/autostart.py) — biases toward anime popular/rated
# enough to plausibly have screenshots on Shikimori/Jikan/TMDB, and
# toward titles players are more likely to recognize. Not applied to
# search()/get_by_id(), which answer a starter's own explicit query and
# should never silently hide a low-scored title they typed themselves.
RANDOM_PICK_MIN_SCORE = 6.5

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

_RANDOM_QUERY = """
query ($minScore: Float) {
  animes(order: random, limit: 1, score: $minScore, censored: true) {
    id
    name
    russian
    english
    synonyms
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
    status this used to catch via `rest.fetch_by_id`.

    `data.get("animes")` goes through `parsing.parse_entries` before
    anything indexes it, same as `search()` — deliberately, not an
    oversight: a malformed (non-list) `animes` here now raises
    `RuntimeError` ("service is down") instead of silently returning
    `None` ("pick is gone"). An API malfunction being reported as an
    outage rather than as a vanished search result is the correct
    direction — the opposite is exactly the issue #103 failure mode this
    migration exists to eliminate."""
    variables = {"ids": str(shikimori_id)}
    data = await graphql.request(_API, client, query=_DETAIL_QUERY, variables=variables)
    entry = _single_anime(data)
    if entry is None:
        logger.debug("Shikimori id {} no longer found", shikimori_id)
        return None
    return parsing.parse_entry(_API_NAME, entry, _parse_detail_result)


async def random_anime(client: httpx.AsyncClient) -> ShikimoriResult | None:
    """One anime, uniformly at random via Shikimori's own `order: random`
    (confirmed against the published GraphQL schema — also has
    `ranked_random`), filtered to `score >= RANDOM_PICK_MIN_SCORE` and
    `censored: true` (excludes hentai/yaoi/yuri) since this feeds a
    shared group topic — see services/game/autostart.py, the sole
    caller. Reuses the full-detail query shape (title/english/russian/
    synonyms) directly, unlike search()'s light query, since there's no
    picker to show — this is the only pick that will ever be shown.

    Deliberately NOT `@cache.cached()` — see test_random_anime_is_not_cached_across_calls."""
    variables = {"minScore": RANDOM_PICK_MIN_SCORE}
    data = await graphql.request(_API, client, query=_RANDOM_QUERY, variables=variables)
    entry = _single_anime(data)
    if entry is None:
        logger.debug("Shikimori random pick returned nothing")
        return None
    return parsing.parse_entry(_API_NAME, entry, _parse_detail_result)


@cache.cached()
async def screenshots(client: httpx.AsyncClient, shikimori_id: int) -> list[str]:
    """Real in-episode screenshots (not promotional art) for a
    Shikimori-identified anime — used by the screenshot-picker gallery.
    Cached (see cache.py) so repeatedly tapping "More screenshots" for
    the same anime re-slices the same cached list instead of re-hitting
    the API every time — pagination/slicing for display is the caller's
    job, not this function's.

    `data.get("animes")` goes through `_single_anime` (the same
    `parsing.parse_entries` guard `search()` and `get_by_id` use) before
    anything indexes it — deliberately, not an oversight: see
    `get_by_id`'s docstring for why a malformed `animes` here raising
    `RuntimeError` rather than yielding an empty screenshot list is the
    correct direction (issue #103)."""
    variables = {"ids": str(shikimori_id)}
    data = await graphql.request(_API, client, query=_SCREENSHOTS_QUERY, variables=variables)
    entry = _single_anime(data)
    entries = (entry.get("screenshots") or []) if entry else []
    # Through `parse_entries` rather than an inline comprehension:
    # `entry.get("originalUrl")` on a scalar is an AttributeError, and
    # this endpoint's entries are third-party data exactly like the
    # search endpoint's (issue #83).
    urls = parsing.parse_entries(_API_NAME, entries, _parse_screenshot_url)[:SCREENSHOT_FETCH_LIMIT]
    logger.debug("Shikimori id {} has {} screenshot(s) available", shikimori_id, len(entries))
    return urls


def _single_anime(data: dict) -> dict | None:
    """The one `Anime` entry a by-id/screenshots query's `animes` list
    should hold, or None if the id wasn't found (an empty list) or the
    single entry couldn't be read (a non-dict scalar — `parse_entries`
    already logged why).

    Routes `data.get("animes")` through the same `parsing.parse_entries`
    guard `search()` already uses, rather than indexing it directly —
    without this, a truthy non-list `animes` (a dict, a number, a
    malformed string) would reach `[0]`/`.get(...)` unguarded and raise
    a `KeyError`/`TypeError`/`AttributeError` that escapes
    `_SEARCH_SERVICE_ERRORS` entirely (issue #83, reintroduced for this
    call shape). The identity parse function is deliberate: this helper
    only validates the *container*, exactly one level of guard — each
    caller still runs its own `_parse_*` over the single entry
    afterwards."""
    entries = parsing.parse_entries(_API_NAME, data.get("animes"), lambda raw: raw)
    return entries[0] if entries else None


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
    isn't an ASCII digit-only string (missing, null, a float, a bool, an
    array, an object, a non-numeric string, or a non-ASCII "digit" like
    a superscript that `str.isdigit()` accepts but `int()` can't parse)
    raises `TypeError`, which `parse_entry` turns into the same
    WARNING-and-skip as every other malformed id (issue #83)."""
    value = raw["id"]
    # `isascii()` alongside `isdigit()`, not instead of it: `str.isdigit()`
    # returns True for some non-ASCII digit characters `int()` can't
    # actually parse (e.g. "²".isdigit() is True but int("²")
    # raises ValueError, which isn't in parsing._MALFORMED_ENTRY_ERRORS and
    # would escape uncaught). Requiring isascii() too guarantees int(value)
    # succeeds for anything that passes this check.
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
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


def _parse_search_result(raw: dict) -> ShikimoriResult | None:
    """Returns None (skipped quietly, same as any other "not wanted"
    decision) when both `name` and `russian` are empty — see
    `parsing.has_answer_key` (issue #89).

    The search query never asks Shikimori for `english`/`synonyms` (see
    module docstring), so this check can only judge the two title
    variants search actually has — it can't see a synonym or an English
    title that get_by_id might later fetch. In the extremely degenerate
    case of a real anime with both `name` and `russian` null but
    `english` or `synonyms` populated, this drops its picker button a
    step earlier than get_by_id would have. That's a defensible
    tradeoff, not a reopening of issue #89: the bug that issue closes
    (an unwinnable *staged* game) is fully closed by
    `_parse_detail_result` below, the only function whose output ever
    reaches `stage_result()` (see `services/game/state.py`). This
    function only decides whether a "?" button with nothing behind it —
    as far as the light search query can tell — is worth showing at
    all."""
    shikimori_id = _parse_shikimori_id(raw)
    title_romaji = parsing.optional_str(raw, "name")
    title_russian = parsing.optional_str(raw, "russian")
    if not parsing.has_answer_key((title_romaji, title_russian), []):
        logger.debug("Shikimori id {} has no title in name/russian, skipping", shikimori_id)
        return None
    return ShikimoriResult(
        shikimori_id=shikimori_id,
        title_romaji=title_romaji,
        title_english=None,
        title_russian=title_russian,
        synonyms=[],
    )


def _parse_detail_result(raw: dict) -> ShikimoriResult | None:
    """The only call that fills in `english`/`synonyms`, so it's where a
    malformed one actually reaches the game's answer key (issue #86).

    `english` no longer needs the list-indexing dance the REST version
    did: GraphQL's `Anime.english` is a plain nullable `String` scalar
    (the actual fix for issue #103, not just a defensive rewrite), so
    `parsing.optional_str` covers it exactly the way it covers `name`/
    `russian`.

    Returns None (skipped quietly, same as any other "not wanted"
    decision) when every title variant is empty and there are no
    synonyms either — see `parsing.has_answer_key` (issue #89): this is
    the function whose output actually reaches `stage_result()`, so this
    is the check that closes the unwinnable-game bug for real, not just
    for the picker button (see `_parse_search_result` above)."""
    shikimori_id = _parse_shikimori_id(raw)
    title_romaji = parsing.optional_str(raw, "name")
    title_english = parsing.optional_str(raw, "english")
    title_russian = parsing.optional_str(raw, "russian")
    synonyms = parsing.optional_str_list(raw, "synonyms")
    if not parsing.has_answer_key((title_romaji, title_english, title_russian), synonyms):
        logger.debug(
            "Shikimori id {} has no title in any variant and no synonyms, skipping", shikimori_id
        )
        return None
    return ShikimoriResult(
        shikimori_id=shikimori_id,
        title_romaji=title_romaji,
        title_english=title_english,
        title_russian=title_russian,
        synonyms=synonyms,
    )
