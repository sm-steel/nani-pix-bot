"""Tenrai search — the anime-data API replacing Jikan (the unofficial
MyAnimeList REST API this package used until Jikan itself shut down;
see the tenrai-migration plan). Called once per game, at setup time
only, same as this package's anilist.py/shikimori.py/tmdb.py. See
MECHANICS.md's "Starting a game" section.

Tenrai's response schema is confirmed schema-compatible with Jikan's for
every endpoint this module uses — the same `mal_id`/`title`/
`title_english`/`title_japanese`/`title_synonyms`/`rating`/`jpg` shape —
so this module was written as a close structural port of Jikan's own
module rather than a fresh design (that predecessor module, and its
test file, are gone as of this same migration's second step — see git
history for the shape this was ported from). Tenrai's endpoints already
return the richer title/synonym fields on both the search (list) and
detail (by-id) response, same as Jikan's did, so both search() and
get_by_id() parse the same shape here, no forced re-fetch-by-id needed
for the full field set.

Wired into `Provider.TENRAI` (`models/enums.py`) as this migration's
second step, once this module was confirmed correct on its own."""

import sys
from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.search import cache, parsing, rest
from nani_pix_bot.services.search.base import ScreenshotModule

TENRAI_BASE_URL = "https://api.tenrai.org/v1/anime"
TENRAI_RANDOM_URL = "https://api.tenrai.org/v1/random/anime"
SEARCH_RESULT_LIMIT = 5
# A fixed cap on how many pictures are ever fetched/cached per anime —
# not a per-call parameter, so the cache key never needs to encode it
# (the gallery UI, ticket 7, does its own client-side pagination of
# whatever this returns).
SCREENSHOT_FETCH_LIMIT = 20
# A floor on Tenrai/MAL's own "members" count — the Tenrai equivalent of
# shikimori.py's RANDOM_PICK_MIN_WATCHED, applied only to the random
# pick for the same reason: biases away from anime almost nobody has
# actually watched. MAL's user base is much larger than Shikimori's, so
# this is a different scale, not a like-for-like number — a starting
# guess, tune here if it feels wrong in practice.
RANDOM_PICK_MIN_MEMBERS = 5000

# Tenrai's public tier (120 RPM / 4 RPS / 40,000 RPD) is far more than
# this bot's call volume needs, so no X-Server-Key support here — YAGNI.
# The User-Agent below is the same courtesy Jikan's own module used to
# send: identifies this bot as a consumer of a shared resource rather
# than an anonymous default.
_REQUEST_HEADERS = {"User-Agent": "nani-pix-bot (github.com/sm-steel/nani-pix-bot)"}

_API = rest.RestApi(name=Provider.TENRAI.display_name, headers=_REQUEST_HEADERS)


@dataclass(frozen=True)
class TenraiResult:
    tenrai_id: int
    title_romaji: str | None
    title_english: str | None
    title_native: str | None
    synonyms: list[str]
    # Tenrai's own MAL content-rating string (e.g. "Rx - Hentai"), not
    # requested/used by search()'s or get_by_id()'s existing callers —
    # kept solely so services/game/autostart.py can reject an
    # explicit-rated random pick, the same way it already does for
    # Jikan (issue #159), since Tenrai's /random/anime endpoint has no
    # server-side SFW filter the way Shikimori's random_anime() does.
    # Defaults to None so every existing keyword-based TenraiResult(...)
    # construction (tests included) stays valid unchanged.
    rating: str | None = None
    # Tenrai/MAL's own "how many users have this on their list" count —
    # the same signal shikimori.py's RANDOM_PICK_MIN_WATCHED already
    # uses from Shikimori's statusesStats. Used only by random_anime()'s
    # own popularity floor below; defaults to None so every existing
    # keyword-based TenraiResult(...) construction (tests included)
    # stays valid unchanged — same reasoning `rating` already documents
    # for itself.
    members: int | None = None


@cache.cached()
async def search(
    client: httpx.AsyncClient, query: str, *, limit: int = SEARCH_RESULT_LIMIT
) -> list[TenraiResult]:
    """Look up anime on Tenrai whose title matches `query`. A starter
    who edits and resubmits the same query while narrowing it down hits
    the brief cache.cached() TTL (see cache.py) rather than the network
    each time.

    Always asks for `sfw=true`: a picked result here can end up posted
    straight into the shared group topic, so this is the same cheap
    insurance TMDB already gets by default through its `include_adult`
    flag."""
    params = {"q": query, "limit": limit, "sfw": "true"}
    data = await rest.get_json(_API, client, TENRAI_BASE_URL, params)
    results = parsing.parse_entries(_API.name, data.get("data") or [], _parse_result)
    logger.debug("Tenrai search {!r} returned {} result(s)", query, len(results))
    return results


@cache.cached()
async def get_by_id(client: httpx.AsyncClient, tenrai_id: int) -> TenraiResult | None:
    """Fetch one Tenrai anime by id — the call that fires the moment a
    starter taps a Tenrai-picker button on a result search() already
    returned. The brief in-process cache (cache.py) only smooths out a
    double tap within the same run; it's wiped on every restart, so it
    plays no part in the restart-resilient by-id re-fetch design issue
    #11 established elsewhere — that's a separate concern from this
    short-lived speed-up."""
    return await rest.fetch_by_id(
        _API, client, f"{TENRAI_BASE_URL}/{tenrai_id}", tenrai_id, _parse_detail_result
    )


async def random_anime(client: httpx.AsyncClient) -> TenraiResult | None:
    """Pull one anime uniformly at random straight from Tenrai's
    `/random/anime` endpoint. This is the fallback services/game/
    autostart.py reaches for when Shikimori's own random pick came back
    empty or unreachable — see that module for the sole caller.

    The response is shaped like the by-id detail endpoint's
    (`{"data": {...}}`), so it's parsed through the same
    `_parse_detail_result` — confirmed identical at implementation time
    rather than assumed.

    Unlike `get_by_id` above, which gets its protection for free from
    `rest.fetch_by_id`, this endpoint bypasses that helper, so it's
    routed through `parsing.parse_entry` explicitly instead of calling
    `_parse_detail_result(data)` bare — a malformed field (a non-string
    `rating` tripping `optional_str`'s guard, say) then logs a WARNING
    and quietly skips this pick rather than raising out of the JobQueue
    callback that ultimately calls this (see issue #159's final
    review).

    Also sends `sfw=true`, a param Tenrai's random endpoint supports
    (confirmed against its OpenAPI spec) that Jikan's equivalent never
    had — the same shared-topic insurance search() applies above.

    A second, Tenrai-only gate follows the parse: `members >=
    RANDOM_PICK_MIN_MEMBERS`, mirroring shikimori.py's watched-count
    floor so this fallback doesn't surface an anime almost nobody has
    actually watched. A pick with no `members` field at all parses as
    None, which fails that floor exactly like a too-low count would.

    Deliberately NOT `@cache.cached()` — see test_random_anime_is_not_cached_across_calls."""
    data = await rest.get_json(_API, client, TENRAI_RANDOM_URL, {"sfw": "true"})
    result = parsing.parse_entry(_API.name, data, _parse_detail_result)
    if result is None:
        return None
    if result.members is None or result.members < RANDOM_PICK_MIN_MEMBERS:
        logger.debug(
            "Tenrai id {} has only {} members (below floor {}), rejecting",
            result.tenrai_id,
            result.members,
            RANDOM_PICK_MIN_MEMBERS,
        )
        return None
    return result


@cache.cached()
async def screenshots(client: httpx.AsyncClient, tenrai_id: int) -> list[str]:
    """The promotional/episode picture set Tenrai has on file for an
    already-identified anime — not genuine in-episode frame grabs the
    way Shikimori's screenshots are, but good enough to stand in (see
    the "start a game without a screenshot" plan's research). Held in
    the brief cache (cache.py), so tapping "More screenshots" again on
    the same anime slices the same cached list rather than asking the
    API twice."""
    data = await rest.get_json(_API, client, f"{TENRAI_BASE_URL}/{tenrai_id}/pictures", {})
    pictures = data.get("data") or []
    urls = parsing.parse_entries(_API.name, pictures, _picture_url)[:SCREENSHOT_FETCH_LIMIT]
    logger.debug("Tenrai id {} has {} picture(s) available", tenrai_id, len(pictures))
    return urls


# Provider.search_module/screenshot_module's value for Provider.TENRAI —
# see services/search/base.py's module docstring. random_anime() above is
# a direct-import-only helper (services/game/autostart.py), not part of
# this.
service = ScreenshotModule(sys.modules[__name__])


def _picture_url(entry: dict) -> str | None:
    """The declared `str | None` is enforced rather than assumed: this
    result goes straight to `InputMediaPhoto(media=url)` in the gallery,
    and a malformed `{"jpg": {"large_image_url": 5}}` would hand it the
    bare int (the same defect Jikan's own module had — issue #86).

    **Both fields are read before either is chosen**, rather than letting
    `or` short-circuit past the fallback. Short-circuiting made the check
    depend on *which half* a provider had mistyped instead of on whether
    it had: a bad `image_url` behind a good `large_image_url` was kept
    silently, while a bad `large_image_url` in front of a perfectly good
    `image_url` — the case the fallback exists for — skipped the picture.
    One function, two opposite answers to the same malformation.

    Treating them alike, rather than falling back past the bad one, is
    what the rest of this package already does: `_parse_result` below
    skips an entry whose `title_native` is malformed even when `title`
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


def _parse_detail_result(raw: dict) -> TenraiResult | None:
    """Tenrai's by-id endpoint answers with the one entry nested under
    "data", unlike the bare list the search endpoint returns.

    An entry-less body gets the same treatment a 404 would — the picker
    tells the starter the pick is gone — instead of letting a raw
    `KeyError` escape past every handler (the same defect issue #75
    closed in Jikan's own module)."""
    entry = raw.get("data")
    if not entry:
        logger.error('Tenrai answered 200 with no "data" entry to parse')
        return None
    return _parse_result(entry)


def _parse_result(raw: dict) -> TenraiResult | None:
    """Titles and synonyms are validated alongside the id, because the
    guard around this function stops at the `return` — see
    `parsing.optional_str`/`optional_str_list` (issue #86, also closed in
    Jikan's own module).

    Returns None (skipped quietly, same as any other "not wanted"
    decision) when every title variant is empty and there are no
    synonyms either — see `parsing.has_answer_key` (issue #89, also
    closed in Jikan's own module): a well-typed entry with no title and
    no synonyms stages a
    game `match_candidates()` can never match anything against.

    `members` follows tmdb.py's `_parse_season` pattern for a field
    that's optional at the JSON level but validated when present, per
    `parsing.require_int`'s own docstring: a pre-check here rather than
    a new `parsing.py` helper, since it's a one-off field, not a class
    of fields every provider shares."""
    tenrai_id = parsing.require_int(raw, "mal_id")
    title_romaji = parsing.optional_str(raw, "title")
    title_english = parsing.optional_str(raw, "title_english")
    title_native = parsing.optional_str(raw, "title_japanese")
    synonyms = parsing.optional_str_list(raw, "title_synonyms")
    if not parsing.has_answer_key((title_romaji, title_english, title_native), synonyms):
        logger.debug(
            "Tenrai id {} has no title in any variant and no synonyms, skipping", tenrai_id
        )
        return None
    members = None if raw.get("members") is None else parsing.require_int(raw, "members")
    return TenraiResult(
        tenrai_id=tenrai_id,
        title_romaji=title_romaji,
        title_english=title_english,
        title_native=title_native,
        synonyms=synonyms,
        rating=parsing.optional_str(raw, "rating"),
        members=members,
    )
