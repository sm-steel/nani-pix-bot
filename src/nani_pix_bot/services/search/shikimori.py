"""Shikimori search — the Russian-community alternative to AniList,
called once per game at setup time, same as this package's anilist.py. See
MECHANICS.md's "Starting a game" section.

Shikimori's list endpoint (`/api/animes`) is deliberately light — no
`synonyms`/`english` — so a picked result is always re-fetched by id via
the detail endpoint (`/api/animes/:id`) for the full title/synonym set.
This mirrors the restart-resilient by-id re-fetch issue #11 already
established for AniList, except here it's forced by Shikimori's own API
shape rather than a design choice.
"""

from dataclasses import dataclass
from http import HTTPStatus

import httpx
from loguru import logger

from nani_pix_bot.services.search import http_retry

# Shikimori's older shikimori.one domain now permanently 301-redirects
# here — and shikimori.one is itself unreachable directly from moscow,
# while this .io domain is (our httpx client doesn't follow redirects,
# so pointing at the old domain would just return an HTML redirect page
# instead of JSON). See ARCHITECTURE.md's "AniList/Shikimori connectivity".
SHIKIMORI_BASE_URL = "https://shikimori.io/api/animes"
SEARCH_RESULT_LIMIT = 5

# Shikimori asks API consumers to identify themselves with a descriptive
# User-Agent rather than a Referer (unlike AniList) — see the project's
# Shikimori research spike.
_REQUEST_HEADERS = {"User-Agent": "nani-pix-bot (github.com/sm-steel/nani-pix-bot)"}


@dataclass(frozen=True)
class ShikimoriResult:
    shikimori_id: int
    title_romaji: str | None
    title_english: str | None
    title_russian: str | None
    synonyms: list[str]


async def search(
    client: httpx.AsyncClient, query: str, *, limit: int = SEARCH_RESULT_LIMIT
) -> list[ShikimoriResult]:
    """Search Shikimori anime titles matching `query`. The list endpoint
    doesn't return synonyms/english — those are filled in by get_by_id
    once a result is picked."""
    params = {"search": query, "limit": limit}
    entries = await _request(client, url=SHIKIMORI_BASE_URL, params=params)
    results = [_parse_search_result(entry) for entry in entries]
    logger.debug("Shikimori search {!r} returned {} result(s)", query, len(results))
    return results


async def get_by_id(client: httpx.AsyncClient, shikimori_id: int) -> ShikimoriResult | None:
    """Re-fetch a single anime by id — used when the starter taps a
    Shikimori-picker button. See module docstring: this is the only call
    that returns synonyms/english, and (as with AniList) it's re-fetched
    fresh rather than cached in ephemeral bot memory, so a bot restart
    mid-pick doesn't lose anything (see issue #11)."""
    try:
        entry = await _request(client, url=f"{SHIKIMORI_BASE_URL}/{shikimori_id}", params={})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == HTTPStatus.NOT_FOUND:
            logger.debug("Shikimori id {} no longer found", shikimori_id)
            return None
        raise
    return _parse_detail_result(entry)


async def _request(client: httpx.AsyncClient, *, url: str, params: dict) -> dict:
    async def make_request() -> httpx.Response:
        return await client.get(url, params=params, headers=_REQUEST_HEADERS)

    response = await http_retry.request_with_retry(
        make_request, service_name="Shikimori", context=f"url {url!r}, params {params!r}"
    )
    return response.json()


def _parse_search_result(raw: dict) -> ShikimoriResult:
    return ShikimoriResult(
        shikimori_id=raw["id"],
        title_romaji=raw.get("name"),
        title_english=None,
        title_russian=raw.get("russian"),
        synonyms=[],
    )


def _parse_detail_result(raw: dict) -> ShikimoriResult:
    english = raw.get("english") or []
    return ShikimoriResult(
        shikimori_id=raw["id"],
        title_romaji=raw.get("name"),
        title_english=english[0] if english else None,
        title_russian=raw.get("russian"),
        synonyms=raw.get("synonyms") or [],
    )
