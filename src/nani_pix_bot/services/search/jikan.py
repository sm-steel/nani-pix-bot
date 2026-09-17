"""Jikan search — the unofficial MyAnimeList REST API. Called once per
game, at setup time only, same as this package's anilist.py/
shikimori.py. See MECHANICS.md's "Starting a game" section.

Jikan's endpoints already return the richer title/synonym fields on
both the search (list) and detail (by-id) response, unlike Shikimori's
list endpoint — so both search() and get_by_id() parse the same shape
here, no forced re-fetch-by-id needed for the full field set.
"""

from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.search import cache, parsing, rest

JIKAN_BASE_URL = "https://api.jikan.moe/v4/anime"
JIKAN_RANDOM_URL = "https://api.jikan.moe/v4/random/anime"
SEARCH_RESULT_LIMIT = 5
# A fixed cap on how many pictures are ever fetched/cached per anime —
# not a per-call parameter, so the cache key never needs to encode it
# (the gallery UI, ticket 7, does its own client-side pagination of
# whatever this returns).
SCREENSHOT_FETCH_LIMIT = 20

# Jikan doesn't require an API key, but — same courtesy as
# shikimori.py's User-Agent — identifies this bot as a consumer of a
# shared, community-run resource rather than an anonymous default.
_REQUEST_HEADERS = {"User-Agent": "nani-pix-bot (github.com/sm-steel/nani-pix-bot)"}

_API = rest.RestApi(name=Provider.JIKAN.display_name, headers=_REQUEST_HEADERS)


@dataclass(frozen=True)
class JikanResult:
    jikan_id: int
    title_romaji: str | None
    title_english: str | None
    title_native: str | None
    synonyms: list[str]
    # Jikan's own MAL content-rating string (e.g. "Rx - Hentai"), not
    # requested/used by search()'s or get_by_id()'s existing callers —
    # added solely so services/game/autostart.py can reject an
    # explicit-rated random pick (issue #159), since Jikan's
    # /random/anime endpoint has no server-side SFW filter the way
    # Shikimori's random_anime() does. Defaults to None so every
    # existing keyword-based JikanResult(...) construction (tests
    # included) stays valid unchanged.
    rating: str | None = None


@cache.cached()
async def search(
    client: httpx.AsyncClient, query: str, *, limit: int = SEARCH_RESULT_LIMIT
) -> list[JikanResult]:
    """Search Jikan anime titles matching `query`. Cached briefly (see
    cache.py) so a starter repeating the same query doesn't re-hit the
    API each time.

    `sfw=true` because whatever the starter picks here ends up posted
    into a shared group topic — the same insurance TMDB gives for free
    via its `include_adult` default."""
    params = {"q": query, "limit": limit, "sfw": "true"}
    data = await rest.get_json(_API, client, JIKAN_BASE_URL, params)
    results = parsing.parse_entries(_API.name, data.get("data") or [], _parse_result)
    logger.debug("Jikan search {!r} returned {} result(s)", query, len(results))
    return results


@cache.cached()
async def get_by_id(client: httpx.AsyncClient, jikan_id: int) -> JikanResult | None:
    """Re-fetch a single anime by id — used when the starter taps a
    Jikan-picker button. Cached briefly (see cache.py) — a short-lived,
    in-process-only performance optimization, not a substitute for the
    restart-resilient by-id re-fetch pattern issue #11 established
    (that's about not caching in ephemeral bot memory across a
    restart; this cache is wiped on every restart same as everything
    else in it)."""
    return await rest.fetch_by_id(
        _API, client, f"{JIKAN_BASE_URL}/{jikan_id}", jikan_id, _parse_detail_result
    )


async def random_anime(client: httpx.AsyncClient) -> JikanResult | None:
    """One anime, uniformly at random via Jikan's own `/random/anime`
    endpoint — the fallback when Shikimori's random pick is unreachable
    or empty (see services/game/autostart.py, the sole caller).

    Reuses the by-id detail endpoint's response shape/parser
    (`{"data": {...}}`, `_parse_detail_result`) — confirmed to match at
    implementation time.

    Routed through `parsing.parse_entry` (unlike a bare
    `_parse_detail_result(data)` call) so a malformed field — e.g. a
    non-string `rating` tripping `optional_str`'s type guard — logs a
    WARNING and skips this pick rather than raising out of the JobQueue
    callback that ultimately calls this (see issue #159's final review):
    `get_by_id` above gets this same protection for free via
    `rest.fetch_by_id`, which this endpoint doesn't go through.

    Deliberately NOT `@cache.cached()` — see test_random_anime_is_not_cached_across_calls."""
    data = await rest.get_json(_API, client, JIKAN_RANDOM_URL, {})
    return parsing.parse_entry(_API.name, data, _parse_detail_result)


@cache.cached()
async def screenshots(client: httpx.AsyncClient, jikan_id: int) -> list[str]:
    """Promotional/episode pictures for a Jikan-identified anime — not
    true in-episode frame grabs the way Shikimori's are, but usable
    (see the "start a game without a screenshot" plan's research).
    Cached (see cache.py) so repeatedly tapping "More screenshots" for
    the same anime re-slices the same cached list instead of re-hitting
    the API every time."""
    data = await rest.get_json(_API, client, f"{JIKAN_BASE_URL}/{jikan_id}/pictures", {})
    pictures = data.get("data") or []
    urls = parsing.parse_entries(_API.name, pictures, _picture_url)[:SCREENSHOT_FETCH_LIMIT]
    logger.debug("Jikan id {} has {} picture(s) available", jikan_id, len(pictures))
    return urls


def _picture_url(entry: dict) -> str | None:
    """The declared `str | None` is enforced rather than assumed: this
    result goes straight to `InputMediaPhoto(media=url)` in the gallery,
    and `{"jpg": {"large_image_url": 5}}` handed it the bare int (#86).

    **Both fields are read before either is chosen**, rather than letting
    `or` short-circuit past the fallback. Short-circuiting made the check
    depend on *which half* a provider had mistyped instead of on whether
    it had: a bad `image_url` behind a good `large_image_url` was kept
    silently, while a bad `large_image_url` in front of a perfectly good
    `image_url` — the case the fallback exists for — skipped the picture.
    One function, two opposite answers to the same malformation.

    Treating them alike, rather than falling back past the bad one, is
    what the rest of this package already does: `_parse_result` above
    skips an entry whose `title_japanese` is malformed even when `title`
    would have displayed fine. A malformed sibling field is a malformed
    entry, and per-field salvage is the second mechanism `parsing.py`'s
    docstring argues against. It also keeps the WARNING, which is the
    only signal that a provider has quietly changed its schema — and
    the cost here is the mildest in the package: one picture out of a
    gallery of twenty, not a game."""
    jpg = entry.get("jpg") or {}
    preferred = parsing.optional_str(jpg, "large_image_url")
    fallback = parsing.optional_str(jpg, "image_url")
    return preferred or fallback


def _parse_detail_result(raw: dict) -> JikanResult | None:
    """The by-id endpoint wraps its single entry in a "data" object,
    unlike the search endpoint's list of bare entries.

    A body with no entry in it is reported the same way a 404 is — the
    picker says the pick is gone — rather than raising a `KeyError` no
    handler catches (issue #75)."""
    entry = raw.get("data")
    if not entry:
        logger.error('Jikan answered 200 with no "data" entry to parse')
        return None
    return _parse_result(entry)


def _parse_result(raw: dict) -> JikanResult | None:
    """Titles and synonyms are validated alongside the id, because the
    guard around this function stops at the `return` — see
    `parsing.optional_str`/`optional_str_list` (issue #86).

    Returns None (skipped quietly, same as any other "not wanted"
    decision) when every title variant is empty and there are no
    synonyms either — see `parsing.has_answer_key` (issue #89): a
    well-typed entry with no title and no synonyms stages a game
    `match_candidates()` can never match anything against."""
    jikan_id = parsing.require_int(raw, "mal_id")
    title_romaji = parsing.optional_str(raw, "title")
    title_english = parsing.optional_str(raw, "title_english")
    title_native = parsing.optional_str(raw, "title_japanese")
    synonyms = parsing.optional_str_list(raw, "title_synonyms")
    if not parsing.has_answer_key((title_romaji, title_english, title_native), synonyms):
        logger.debug("Jikan id {} has no title in any variant and no synonyms, skipping", jikan_id)
        return None
    return JikanResult(
        jikan_id=jikan_id,
        title_romaji=title_romaji,
        title_english=title_english,
        title_native=title_native,
        synonyms=synonyms,
        rating=parsing.optional_str(raw, "rating"),
    )
