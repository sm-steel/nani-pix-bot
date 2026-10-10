"""Genre/theme tags for a gated pick (seasons spec §5, P0): Tenrai first —
tried twice, it drops ~1 call in 5 — then Shikimori. Both failing is
"unavailable", never "off-theme"."""

from dataclasses import dataclass

import httpx
from loguru import logger

from nani_pix_bot.services.search import shikimori, tenrai
from nani_pix_bot.services.search.tags import AnimeTag

TAG_ERRORS = (httpx.HTTPError, RuntimeError)
TENRAI_TRIES = 2


class TagsUnavailableError(Exception):
    """Neither provider answered."""


@dataclass(frozen=True)
class TagClients:
    shikimori: httpx.AsyncClient
    tenrai: httpx.AsyncClient


async def fetch_tags(clients: TagClients, mal_id: int) -> list[AnimeTag] | None:
    for attempt in range(1, TENRAI_TRIES + 1):
        try:
            return await tenrai.get_tags(clients.tenrai, mal_id)
        except TAG_ERRORS as exc:
            logger.warning(
                "Tenrai tags for MAL {mal_id} failed (try {attempt}): {error!r}",
                mal_id=mal_id,
                attempt=attempt,
                error=exc,
            )
    try:
        return await shikimori.get_tags(clients.shikimori, mal_id)
    except TAG_ERRORS as exc:
        logger.error(
            "no provider answered for MAL {mal_id}'s tags: {error!r}", mal_id=mal_id, error=exc
        )
        raise TagsUnavailableError from exc
