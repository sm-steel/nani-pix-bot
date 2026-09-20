"""Bot-initiated game picking: choosing a random anime and a screenshot
for it, with a capped retry loop and zero DB writes. Framework-agnostic
per this project's services/ rule — no telegram imports, no DB session.
See jobs/timers/autostart.py for the Telegram/DB-aware orchestration
layer built on top of this (the actual "claim the game" side effects)."""

import secrets
from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.game.state import (
    SCREENSHOT_CAPABLE_PROVIDERS,
    TitleVariants,
    prioritized_title,
)
from nani_pix_bot.services.search import jikan, shikimori
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult

# ~12%, within the 10-15% range this feature was designed to — see the
# design doc for issue #159. Tune here if it feels wrong in practice.
OVERTHROW_PROBABILITY = 0.12
# Different-anime attempts per firing before giving up silently — caps
# how long/how many API calls one "bot tries to start a game" attempt
# can cost if a provider is down or an anime has no screenshots
# anywhere.
AUTOSTART_ATTEMPT_LIMIT = 3

# "The provider is unreachable" — the services/-layer subset of
# commands/dm_start/_shared.py's _SEARCH_SERVICE_ERRORS (no TelegramError
# here: services/ can't import telegram, and nothing in this module makes
# a Telegram call).
_AUTOSTART_SERVICE_ERRORS = (httpx.HTTPError, RuntimeError)


def roll_overthrow() -> bool:
    """A single weighted coin flip for the "overthrow" trigger — see
    jobs/timers/autostart.py's maybe_overthrow(). Uses `secrets`, the
    same RNG source services/i18n.py's variation-pool pick already uses
    in this codebase, rather than introducing a second RNG source."""
    return secrets.randbelow(10_000) < round(OVERTHROW_PROBABILITY * 10_000)


@dataclass(frozen=True)
class AnimePick:
    result: ShikimoriResult | JikanResult
    source: Provider


@dataclass(frozen=True)
class ScreenshotPick:
    provider: Provider
    provider_id: int
    image_bytes_a: bytes
    image_bytes_b: bytes


@dataclass(frozen=True)
class GatheredPick:
    anime: AnimePick
    screenshot: ScreenshotPick


async def gather_pick(
    search_client: httpx.AsyncClient, tmdb_client: httpx.AsyncClient
) -> GatheredPick | None:
    """Up to AUTOSTART_ATTEMPT_LIMIT different-anime attempts: a random
    anime (Shikimori, falling back to Jikan on any failure or empty
    result) plus a random screenshot for it (same-provider-first, then
    the existing fallback order). Never writes to a DB — a caller only
    gets a GatheredPick once a full, valid (anime + downloaded
    screenshot bytes) pick is in hand, which is what keeps a failed
    attempt from ever leaving a partial/broken Game row."""
    for attempt in range(1, AUTOSTART_ATTEMPT_LIMIT + 1):
        anime = await _pick_random_anime(search_client)
        if anime is None:
            logger.warning(
                "Bot autostart pick attempt {}/{}: no random anime available",
                attempt,
                AUTOSTART_ATTEMPT_LIMIT,
            )
            continue
        screenshot = await _pick_screenshot(search_client, tmdb_client, anime)
        if screenshot is None:
            logger.warning(
                "Bot autostart pick attempt {}/{}: no screenshot found for {!r}",
                attempt,
                AUTOSTART_ATTEMPT_LIMIT,
                anime.result,
            )
            continue
        return GatheredPick(anime=anime, screenshot=screenshot)
    logger.error(
        "Bot autostart: exhausted {} attempt(s) with no usable pick", AUTOSTART_ATTEMPT_LIMIT
    )
    return None


def _is_explicit(result: JikanResult) -> bool:
    """Jikan's own convention for adult content — see the rating field's
    documented values on MAL; "Rx" is the sole adult-content prefix.
    Shikimori's random_anime() already filters this server-side
    (censored: true in its GraphQL query) — Jikan's REST /random/anime
    endpoint has no equivalent query parameter, so this is the only
    place that can catch it before a pick reaches the group topic.

    Deliberately matches Shikimori's `censored: true` scope — hentai
    only, not mild-content ratings like `R+ - Mild Nudity`, which this
    intentionally lets through unrejected."""
    return result.rating is not None and result.rating.startswith("Rx")


async def _pick_random_anime(search_client: httpx.AsyncClient) -> AnimePick | None:
    try:
        shikimori_result = await shikimori.random_anime(search_client)
    except _AUTOSTART_SERVICE_ERRORS as exc:
        logger.warning("Shikimori random-anime pick failed, falling back to Jikan: {}", exc)
        shikimori_result = None
    if shikimori_result is not None:
        return AnimePick(result=shikimori_result, source=Provider.SHIKIMORI)

    try:
        jikan_result = await jikan.random_anime(search_client)
    except _AUTOSTART_SERVICE_ERRORS as exc:
        logger.warning("Jikan random-anime pick failed: {}", exc)
        return None
    if jikan_result is None:
        return None
    if _is_explicit(jikan_result):
        logger.warning(
            "Jikan random-anime pick {} is explicit-rated ({!r}), rejecting",
            jikan_result.jikan_id,
            jikan_result.rating,
        )
        return None
    return AnimePick(result=jikan_result, source=Provider.JIKAN)


def _screenshot_provider_order(identified_by: Provider) -> list[Provider]:
    ordered = list(SCREENSHOT_CAPABLE_PROVIDERS)
    if identified_by in ordered:
        ordered.remove(identified_by)
        ordered.insert(0, identified_by)
    return ordered


def _title_variants(result: ShikimoriResult | JikanResult) -> TitleVariants:
    if isinstance(result, ShikimoriResult):
        return TitleVariants(
            english=result.title_english, romaji=result.title_romaji, russian=result.title_russian
        )
    return TitleVariants(
        english=result.title_english, romaji=result.title_romaji, native=result.title_native
    )


async def _cross_search_id(client: httpx.AsyncClient, provider: Provider, title: str) -> int | None:
    """The picked anime's id under `provider`, via the same "search by
    title, take the top result" rule this codebase's human cross-
    provider screenshot resolution already uses (see MECHANICS.md's
    "Picking a screenshot")."""
    try:
        results = await provider.search_module.search(client, title)
    except _AUTOSTART_SERVICE_ERRORS as exc:
        logger.warning("{} cross-search for a screenshot failed: {}", provider.display_name, exc)
        return None
    if not results:
        return None
    return getattr(results[0], provider.id_attr_name)


async def _download_screenshot(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url)
    response.raise_for_status()
    return response.content


async def _resolve_provider_id(
    search_client: httpx.AsyncClient,
    tmdb_client: httpx.AsyncClient,
    anime: AnimePick,
    provider: Provider,
    title: str,
) -> tuple[httpx.AsyncClient, int | None]:
    """Which client to use for `provider`, plus the picked anime's id
    under it — same-provider-as-identification needs no lookup at all;
    any other provider goes through cross-search (see _cross_search_id).
    Split out of _pick_screenshot to keep its own complexity low (qlty
    smells)."""
    client = tmdb_client if provider is Provider.TMDB else search_client
    if provider == anime.source:
        return client, getattr(anime.result, provider.id_attr_name)
    return client, await _cross_search_id(client, provider, title)


async def _fetch_screenshot_url_pair(
    client: httpx.AsyncClient, provider: Provider, provider_id: int
) -> tuple[str, str] | None:
    """Two DISTINCT screenshot URLs for `provider_id` under `provider`,
    or None if the fetch failed, came back empty, or has fewer than 2
    URLs to choose from — HARD MODE always needs a genuine pair, never
    the same screenshot twice. Every autostart pick is unconditionally
    a pair-pick now, replacing the old single-URL _fetch_screenshot_url.

    Uses secrets.SystemRandom().sample() over INDICES, not the URL
    values themselves — sampling by value could let two textually
    identical URL strings pass as "distinct" by accident. Everywhere
    else in this module still uses plain secrets.choice() for a
    single pick; this is the one place two-at-once sampling is
    actually needed. Split out of _pick_screenshot to keep its own
    complexity low (qlty smells)."""
    try:
        urls = await provider.screenshot_module.screenshots(client, provider_id)
    except _AUTOSTART_SERVICE_ERRORS as exc:
        logger.warning("{} screenshot fetch failed: {}", provider.display_name, exc)
        return None
    if len(urls) < 2:
        return None
    i, j = secrets.SystemRandom().sample(range(len(urls)), 2)
    return urls[i], urls[j]


async def _try_provider(
    search_client: httpx.AsyncClient,
    tmdb_client: httpx.AsyncClient,
    anime: AnimePick,
    provider: Provider,
    title: str,
) -> ScreenshotPick | None:
    """One screenshot-provider attempt within _pick_screenshot's loop:
    resolve an id under `provider`, find a pair of screenshot URLs for
    it, and download both. None at any step means this provider didn't
    pan out — the caller moves on to the next one in the fallback
    order. A failed second download discards the first download's
    bytes entirely rather than returning a partial pick. Split out of
    _pick_screenshot to keep its own complexity low (qlty smells)."""
    client, provider_id = await _resolve_provider_id(
        search_client, tmdb_client, anime, provider, title
    )
    if provider_id is None:
        return None

    url_pair = await _fetch_screenshot_url_pair(client, provider, provider_id)
    if url_pair is None:
        return None
    url_a, url_b = url_pair

    try:
        image_bytes_a = await _download_screenshot(client, url_a)
        image_bytes_b = await _download_screenshot(client, url_b)
    except _AUTOSTART_SERVICE_ERRORS as exc:
        logger.warning("Downloading screenshot pair ({!r}, {!r}) failed: {}", url_a, url_b, exc)
        return None
    return ScreenshotPick(
        provider=provider,
        provider_id=provider_id,
        image_bytes_a=image_bytes_a,
        image_bytes_b=image_bytes_b,
    )


async def _pick_screenshot(
    search_client: httpx.AsyncClient, tmdb_client: httpx.AsyncClient, anime: AnimePick
) -> ScreenshotPick | None:
    title = prioritized_title(_title_variants(anime.result), lang="en")
    for provider in _screenshot_provider_order(anime.source):
        pick = await _try_provider(search_client, tmdb_client, anime, provider, title)
        if pick is not None:
            return pick
    return None
