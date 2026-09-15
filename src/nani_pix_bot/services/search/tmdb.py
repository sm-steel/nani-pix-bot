"""TMDB search — anime are modeled as regular TV shows in TMDB's own
schema, so this hits `/search/tv`/`/tv/{id}` rather than a
movie/anime-specific endpoint. Called once per game, at setup time
only, same as this package's anilist.py/shikimori.py/jikan.py.

Unlike every other provider in this package, TMDB requires an API key
(a v4 "Read Access Token", Bearer-auth) and is DNS-blocked directly
from `moscow` — both handled by the caller's client construction
(`app.py` builds a dedicated proxied client with the token already set
as a default `Authorization` header), not by this module. See
ARCHITECTURE.md's connectivity section.

No synonyms/romaji field: TMDB has no distinct "romaji title" concept,
and fetching alternative titles would need a second API call per
result — out of scope for now (see the "start a game without a
screenshot" plan). `title_romaji`/`synonyms` are always None/`[]`,
kept on `TMDBResult` only so it has the same shape `stage_result()`
already expects from every other provider's result dataclass.
"""

import asyncio
from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.search import cache, parsing, rest

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/original"
SEARCH_RESULT_LIMIT = 5
# A fixed cap on how many episode stills are ever fetched/cached per
# show — not a per-call parameter, so the cache key never needs to
# encode it (the gallery UI, ticket 7, does its own client-side
# pagination of whatever this returns). Also bounds how many extra
# per-episode API calls screenshots() makes (see its docstring) —
# across *all* of a show's seasons, not per season, so a long-running
# show doesn't turn into an unbounded walk.
SCREENSHOT_FETCH_LIMIT = 20
# How many of those per-episode calls may be in flight at once. Not
# `SCREENSHOT_FETCH_LIMIT` (i.e. all of them): every TMDB request goes
# through the single `amsterdam` tinyproxy that Telegram's own traffic
# shares (see ARCHITECTURE.md's connectivity section), so 20 simultaneous
# tunnels would be rude to the proxy and to the bot's own polling. 5 turns
# the worst case from 20 sequential round trips (minutes, with the starter
# staring at nothing) into 4 waves, well inside TMDB's rate limit.
SCREENSHOT_FETCH_CONCURRENCY = 5

# No per-request headers: the v4 Read Access Token is set as a default
# Authorization header on the client itself (app.py), not here.
_API = rest.RestApi(name=Provider.TMDB.display_name)


@dataclass(frozen=True)
class TMDBResult:
    tmdb_id: int
    title_romaji: str | None
    title_english: str | None
    title_native: str | None
    synonyms: list[str]


@cache.cached()
async def search(
    client: httpx.AsyncClient, query: str, *, limit: int = SEARCH_RESULT_LIMIT
) -> list[TMDBResult]:
    """Search TMDB TV shows matching `query`. TMDB paginates at a fixed
    20-per-page rather than taking a result-count param, so `limit` is
    applied client-side. Cached briefly (see cache.py) so a starter
    repeating the same query doesn't re-hit the API each time."""
    data = await rest.get_json(_API, client, f"{TMDB_BASE_URL}/search/tv", {"query": query})
    results = parsing.parse_entries(_API.name, data.get("results") or [], _parse_result)[:limit]
    logger.debug("TMDB search {!r} returned {} result(s)", query, len(results))
    return results


@cache.cached()
async def get_by_id(client: httpx.AsyncClient, tmdb_id: int) -> TMDBResult | None:
    """Re-fetch a single show by id — used when the starter taps a
    TMDB-picker button. Cached briefly (see cache.py) — a short-lived,
    in-process-only performance optimization, not a substitute for the
    restart-resilient by-id re-fetch pattern issue #11 established."""
    return await rest.fetch_by_id(
        _API, client, f"{TMDB_BASE_URL}/tv/{tmdb_id}", tmdb_id, _parse_result
    )


@cache.cached()
async def screenshots(client: httpx.AsyncClient, tmdb_id: int) -> list[str]:
    """Real per-episode stills (not promotional art) for a
    TMDB-identified show — used by the screenshot-picker gallery.
    Unlike Shikimori/Jikan's single-call screenshot endpoints, TMDB has
    no bulk "all stills for this show" resource: the per-episode detail
    endpoint (`/tv/{id}/season/{s}/episode/{e}`) already includes a
    `still_path` directly, so this fetches the show's season list once,
    then one extra call per episode (up to SCREENSHOT_FETCH_LIMIT
    across every season, see `_episode_targets`) — more chatty than the
    other two providers, but the whole result is cached as one unit
    (see cache.py) so repeating this for the same show costs nothing
    further until the TTL expires.

    Those per-episode calls run concurrently, bounded by
    SCREENSHOT_FETCH_CONCURRENCY: sequentially they were up to 20 round
    trips through the `amsterdam` proxy, each with the client's 30s
    timeout, which is minutes of a starter waiting on a gallery.

    **The returned order is the episode order, never the completion
    order** — each fetch writes into its own slot of a pre-sized list,
    indexed by the position `_episode_targets` gave it. That's a
    correctness requirement, not a nicety: the gallery resolves a
    `screenshot_pick:tmdb:<index>` callback against this list's indices
    (see cache.py's TTL note), so a list reordered by which request
    happened to answer first would hand the starter a different image
    than the one they tapped.

    A `TaskGroup` rather than `asyncio.gather`: gather propagates the
    first failure but does *not* cancel its siblings, so a show whose
    second episode 500s still issued all twenty requests, eleven of them
    completing after the caller had already raised and replied. The
    serial loop this replaced at least stopped asking. A TaskGroup
    cancels the rest, so at most one queued episode per failing request
    slips through before the abort — each failing task frees its own
    semaphore slot while unwinding, and the episode waiting on that slot
    resumes before the group's cancellation reaches it. Bounded by the
    number of failures, then, not a flat +1, and never worse than gather
    was. What it buys is the requests that are never issued at all, which
    matters precisely because every one of them contends with Telegram's
    own polling through the single `amsterdam` proxy, on the exact path
    where the remote is already misbehaving."""
    show = await rest.get_json(_API, client, f"{TMDB_BASE_URL}/tv/{tmdb_id}", {})
    targets = _episode_targets(show)
    if not targets:
        logger.debug("TMDB id {} has no season episodes to pull stills from", tmdb_id)
        return []

    semaphore = asyncio.Semaphore(SCREENSHOT_FETCH_CONCURRENCY)
    stills: list[str | None] = [None] * len(targets)

    async def fetch_still(index: int, season_number: int, episode_number: int) -> None:
        async with semaphore:
            episode = await rest.get_json(
                _API,
                client,
                f"{TMDB_BASE_URL}/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}",
                {},
            )
        # Through `parse_entry` like every other provider's screenshot
        # read: the still is third-party data, and this one sits inside
        # the TaskGroup rather than inside a `_parse_*` call, so a raise
        # here would leave the group as a `TypeError` that
        # `_SEARCH_SERVICE_ERRORS` doesn't match — a dead keyboard for
        # one bad episode out of twenty (issue #86).
        stills[index] = parsing.parse_entry(_API.name, episode, _parse_still_url)

    try:
        async with asyncio.TaskGroup() as group:
            for index, (season_number, episode_number) in enumerate(targets):
                group.create_task(fetch_still(index, season_number, episode_number))
    except BaseExceptionGroup as failures:
        # Unwrapped, because a TaskGroup reports failures as a group and
        # `_SEARCH_SERVICE_ERRORS` (httpx.HTTPError, RuntimeError) matches
        # the provider's own exception, not a group wrapping it — leaving
        # it wrapped would strand the starter on a dead keyboard, the same
        # outcome issue #75 closed.
        #
        # Every cause is logged, not just the one re-raised: `from None`
        # suppresses the group as context, so the caller's
        # `logger.exception` renders only the first traceback and a
        # mixed-cause outage (a timeout, a 500 and a proxy error page at
        # once) would otherwise show an operator one of three.
        #
        # This unwrap is single-level, which holds only while nothing
        # under `rest.get_json` can raise a group of its own. Putting a
        # nested TaskGroup or an anyio-based client inside a provider call
        # would make `exceptions[0]` itself a group, and hand the caller
        # something `_SEARCH_SERVICE_ERRORS` doesn't match — #75's door,
        # reopened quietly. Flatten here if that day comes.
        logger.warning(
            "TMDB id {} still fetch abandoned: {} of {} episode request(s) failed: {}",
            tmdb_id,
            len(failures.exceptions),
            len(targets),
            [repr(failure) for failure in failures.exceptions],
        )
        raise failures.exceptions[0] from None

    urls = [url for url in stills if url is not None]
    logger.debug(
        "TMDB id {} yielded {} still(s) from {} episode(s)", tmdb_id, len(urls), len(targets)
    )
    return urls


def _episode_targets(show: dict) -> list[tuple[int, int]]:
    """The `(season_number, episode_number)` pairs to ask for stills,
    in ascending season-then-episode order and capped at
    SCREENSHOT_FETCH_LIMIT in total.

    Every season is considered, not just `seasons[0]`: a show whose
    first season carries no `still_path` values used to yield an empty
    gallery even when a later season was full. Specials (season 0) stay
    excluded — they're OVAs/recaps, not the show.

    Seasons are sorted rather than trusted to arrive in order, and the
    cap is applied to the flattened list rather than per season, so the
    result is a deterministic prefix of "every episode this show has"
    however TMDB chose to order its own array.

    The season array is third-party data like any result list, so it
    goes through `parsing.parse_entries` too — a scalar in there would
    otherwise be an `AttributeError` (issue #83)."""
    seasons = parsing.parse_entries(_API.name, show.get("seasons") or [], _parse_season)
    seasons.sort(key=lambda season: season[0])

    targets: list[tuple[int, int]] = []
    for season_number, episode_count in seasons:
        remaining = SCREENSHOT_FETCH_LIMIT - len(targets)
        if remaining <= 0:
            break
        targets.extend(
            (season_number, episode_number)
            for episode_number in range(1, min(episode_count, remaining) + 1)
        )
    return targets


def _parse_season(raw: dict) -> tuple[int, int] | None:
    """One season as `(season_number, episode_count)`, or None for a
    season this function deliberately doesn't want — a special (season 0)
    or a placeholder TMDB sent with a null number.

    A null or absent field still means "no season here" / "no episodes",
    quietly: TMDB sends both keys present-but-null for placeholder seasons,
    and `None >= 1` / `range(1, None + 1)` are both a `TypeError` no handler
    catches (issue #76). That `is None` pre-check is how an optional field
    keeps its default while still being type-checked when it *is* present.

    **Both** fields are then validated rather than left to the guard around
    this function, because the guard's boundary is this `return` — it covers
    reading a field, not the value handed back. `_episode_targets` consumes
    the count at `min(episode_count, remaining)` after the guard has let go,
    so `"3"` escaped as a raw `TypeError`. `season_number` looks safer
    because it's compared right here, and for `str`/`list`/`dict` it is — but
    `2.5` and `True` compare against 1 without raising and were interpolated
    straight into a request URL (`.../season/2.5/episode/1`). That only ever
    looked fine because the bogus URL 404s into `httpx.HTTPStatusError`,
    which costs the starter a "service is down" reply for a show whose other
    seasons were perfectly good — strictly worse than skipping the one bad
    season, and it made this function's `-> tuple[int, int]` a lie (#83)."""
    if raw.get("season_number") is None:
        return None
    season_number = parsing.require_int(raw, "season_number")
    if season_number < 1:
        return None
    if raw.get("episode_count") is None:
        return season_number, 0
    return season_number, parsing.require_int(raw, "episode_count")


def _parse_still_url(episode: dict) -> str | None:
    """One episode's still as a full image URL, or None for an episode
    that simply has no still — the same decision-not-malformation split
    `shikimori._parse_screenshot_url` draws, and the same reason: the
    f-string interpolates whatever it's given, so an unvalidated
    `still_path` reached `InputMediaPhoto(media=url)` as a stringified
    object (issue #86)."""
    still_path = parsing.optional_str(episode, "still_path")
    return f"{TMDB_IMAGE_BASE_URL}{still_path}" if still_path else None


def _parse_result(raw: dict) -> TMDBResult:
    """TMDB has no synonyms field to corrupt (see the module docstring),
    but its two title fields are validated for the same reason every
    other provider's are — the entry guard ends at this `return`
    (issue #86)."""
    return TMDBResult(
        tmdb_id=parsing.require_int(raw, "id"),
        title_romaji=None,
        title_english=parsing.optional_str(raw, "name"),
        title_native=parsing.optional_str(raw, "original_name"),
        synonyms=[],
    )
