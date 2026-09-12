"""Small helpers shared by more than one submodule of this package."""

import re

import httpx

from nani_pix_bot.commands.dm_start.keyboards import method_selection_keyboard
from nani_pix_bot.services import i18n

# Raised by anilist.py/shikimori.py on network failure or exhausted
# rate-limit retries — see their _request() helpers.
_SEARCH_SERVICE_ERRORS = (httpx.HTTPError, RuntimeError)

_SERVICE_DISPLAY_NAMES = {"anilist": "AniList", "shikimori": "Shikimori"}

# Used by both manual.py's second-message step and preview.py's
# "add a synonym" step.
_SYNONYM_SPLIT_RE = re.compile(r"[,\n]")


def _prefer_shikimori(lang: str) -> bool:
    return lang.upper() == "RU"


def _method_prompt_key(*, prefer_shikimori: bool) -> str:
    return (
        "dm_start.pick_method_prompt_shikimori_preferred"
        if prefer_shikimori
        else "dm_start.pick_method_prompt"
    )


async def _reply_service_down(send, lang: str, source: str) -> None:
    """Shared failure path for both the search step and the pick step:
    tell the starter the chosen service looks unreachable and hand them
    back the method-selection keyboard rather than leaving them stuck
    with a dead-end SETUP game (see issue #11's orphaned-row incident)."""
    prefer_shikimori = _prefer_shikimori(lang)
    await send(
        i18n.t("dm_start.search_failed", lang, service=_SERVICE_DISPLAY_NAMES[source]),
        reply_markup=method_selection_keyboard(prefer_shikimori=prefer_shikimori, lang=lang),
    )
