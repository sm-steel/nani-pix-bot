"""More names for an identified anime, from the providers it wasn't
picked from (issue #347) — offered to the game's creator as extra
accepted answers before the preview (see MECHANICS.md's "Starting a
game").

Linked by MyAnimeList id wherever possible, since that's exact: a
Shikimori or Tenrai id *is* the MAL id, and AniList reports its own
entry's `idMal`. Only a TMDB or manual pick has no id to go on; those
search Tenrai by title and accept a result only when a title matches
with its numbers counting, so a sequel ("… 2nd Season") is never taken
for the season the creator meant.

Every network call can fail; the caller treats any failure as "nothing
found" and skips the step — this module never decides that on its own
beyond logging which provider failed."""

import asyncio
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services import matching
from nani_pix_bot.services.search import anilist, shikimori, tenrai

# Most suggestions shown at once — each is one keyboard row.
MAX_SUGGESTIONS = 10

_NAME_FIELDS = ("title_romaji", "title_english", "title_native", "title_russian")


@dataclass(frozen=True)
class AliasLookup:
    """What a search starts from, read off the setup game while its
    session was open."""

    source: str | None
    anilist_id: int | None
    shikimori_id: int | None
    tenrai_id: int | None
    title: str | None  # searched by title when there's no id to link by
    accepted: tuple[str, ...]  # already accepted answers (match_candidates)


@dataclass(frozen=True)
class AliasClients:
    anilist: httpx.AsyncClient
    shikimori: httpx.AsyncClient
    tenrai: httpx.AsyncClient


def names_of(result: object) -> list[str]:
    """Every title variant and synonym a provider result carries."""
    titles = [getattr(result, field, None) for field in _NAME_FIELDS]
    return [title for title in titles if title] + list(getattr(result, "synonyms", []))


def _key(name: str) -> str:
    return matching.normalize_for_match(name, keep_numbers=True)


def merge_suggestions(
    accepted: Iterable[str], found: Iterable[str], *, limit: int = MAX_SUGGESTIONS
) -> list[str]:
    """`found` names not already accepted, each once (compared the way
    matching compares them), in order, at most `limit`."""
    seen = {_key(name) for name in accepted}
    suggestions: list[str] = []
    for raw in found:
        name = raw.strip()
        key = _key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        suggestions.append(name)
    return suggestions[:limit]


async def _mal_id_by_title(clients: AliasClients, title: str) -> int | None:
    """An exact match only (as matching normalizes, numbers kept): fuzzy
    would take "Frieren 2nd Season" for "Frieren"."""
    wanted = _key(title)
    for result in await tenrai.search(clients.tenrai, title):
        names = [result.title_romaji, result.title_english, *result.synonyms]
        if any(name and _key(name) == wanted for name in names):
            return result.tenrai_id
    logger.debug("no Tenrai entry matches {title!r} — no MAL id to link by", title=title)
    return None


async def mal_id(clients: AliasClients, lookup: AliasLookup) -> int | None:
    """The anime's MyAnimeList id: a Shikimori or Tenrai id as is, AniList's
    `idMal`, or an exact Tenrai title match. A lookup by AniList id or by
    title goes over the network and can raise that provider's errors."""
    if lookup.shikimori_id is not None:
        return lookup.shikimori_id
    if lookup.tenrai_id is not None:
        return lookup.tenrai_id
    if lookup.anilist_id is not None:
        result = await anilist.get_by_id(clients.anilist, lookup.anilist_id)
        return result.mal_id if result is not None else None
    if lookup.title:
        return await _mal_id_by_title(clients, lookup.title)
    return None


def _fetches(clients: AliasClients, lookup: AliasLookup, mal_id: int) -> dict[str, Awaitable]:
    """One by-id fetch per provider the game wasn't picked from."""
    fetches: dict[str, Awaitable] = {}
    if lookup.source != Provider.ANILIST:
        fetches[Provider.ANILIST.display_name] = anilist.get_by_mal_id(clients.anilist, mal_id)
    if lookup.source != Provider.SHIKIMORI:
        fetches[Provider.SHIKIMORI.display_name] = shikimori.get_by_id(clients.shikimori, mal_id)
    if lookup.source != Provider.TENRAI:
        fetches[Provider.TENRAI.display_name] = tenrai.get_by_id(clients.tenrai, mal_id)
    return fetches


async def find_aliases(clients: AliasClients, lookup: AliasLookup) -> list[str]:
    """Names the other providers know this anime by that aren't accepted
    yet. One provider failing doesn't sink the others; finding the MAL
    id failing does raise, for the caller to treat as "nothing found"."""
    found_id = await mal_id(clients, lookup)
    if found_id is None:
        logger.info("no MAL id to look up more names by")
        return []
    fetches = _fetches(clients, lookup, found_id)
    results = await asyncio.gather(*fetches.values(), return_exceptions=True)
    found: list[str] = []
    for provider, result in zip(fetches, results, strict=True):
        if isinstance(result, Exception):
            logger.error(
                "{provider} lookup of MAL id {mal_id} failed: {error!r}",
                provider=provider,
                mal_id=found_id,
                error=result,
            )
        elif result is not None:
            logger.debug("{provider} knows MAL id {mal_id}", provider=provider, mal_id=found_id)
            found.extend(names_of(result))
    return merge_suggestions(lookup.accepted, found)
