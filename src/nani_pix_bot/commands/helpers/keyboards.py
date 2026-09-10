"""Inline-keyboard building for the DM anime-identification flow — see
MECHANICS.md's "Starting a game" section."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.services.anilist import AniListResult
from nani_pix_bot.services.shikimori import ShikimoriResult

RETRY_CALLBACK_DATA = "anilist_retry"
_ANILIST_PICK_PREFIX = "anilist_pick:"
_SHIKIMORI_PICK_PREFIX = "shikimori_pick:"

ANILIST_METHOD_CALLBACK_DATA = "method:anilist"
SHIKIMORI_METHOD_CALLBACK_DATA = "method:shikimori"
MANUAL_METHOD_CALLBACK_DATA = "method:manual"


def anilist_results_keyboard(results: list[AniListResult]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                _anilist_label(result), callback_data=f"{_ANILIST_PICK_PREFIX}{result.anilist_id}"
            )
        ]
        for result in results
    ]
    buttons.append(
        [InlineKeyboardButton("None of these — search again", callback_data=RETRY_CALLBACK_DATA)]
    )
    return InlineKeyboardMarkup(buttons)


def shikimori_results_keyboard(results: list[ShikimoriResult]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                _shikimori_label(result),
                callback_data=f"{_SHIKIMORI_PICK_PREFIX}{result.shikimori_id}",
            )
        ]
        for result in results
    ]
    buttons.append(
        [InlineKeyboardButton("None of these — search again", callback_data=RETRY_CALLBACK_DATA)]
    )
    return InlineKeyboardMarkup(buttons)


def _anilist_label(result: AniListResult) -> str:
    title = result.title_english or result.title_romaji or result.title_native or "?"
    return f"{title} ({result.year})" if result.year else title


def _shikimori_label(result: ShikimoriResult) -> str:
    return result.title_russian or result.title_romaji or result.title_english or "?"


def parse_pick_callback_data(data: str) -> tuple[str, int] | None:
    """The picked result's (source, id), or None if `data` wasn't a pick
    (e.g. retry). `source` is "anilist" or "shikimori" — the command
    layer uses it to know which service's get_by_id to re-fetch from
    (restart-resilient, per issue #11)."""
    if data.startswith(_ANILIST_PICK_PREFIX):
        return "anilist", int(data.removeprefix(_ANILIST_PICK_PREFIX))
    if data.startswith(_SHIKIMORI_PICK_PREFIX):
        return "shikimori", int(data.removeprefix(_SHIKIMORI_PICK_PREFIX))
    return None


def method_selection_keyboard(*, prefer_shikimori: bool) -> InlineKeyboardMarkup:
    """AniList vs. Shikimori choice, shown right after the starter's
    photo. Shikimori is offered first for RU-language bots (see
    MECHANICS.md's "Starting a game")."""
    anilist_button = InlineKeyboardButton("AniList", callback_data=ANILIST_METHOD_CALLBACK_DATA)
    shikimori_button = InlineKeyboardButton(
        "Shikimori", callback_data=SHIKIMORI_METHOD_CALLBACK_DATA
    )
    manual_button = InlineKeyboardButton("Manual entry", callback_data=MANUAL_METHOD_CALLBACK_DATA)
    ordered = (
        [shikimori_button, anilist_button]
        if prefer_shikimori
        else [anilist_button, shikimori_button]
    )
    ordered.append(manual_button)
    return InlineKeyboardMarkup([[button] for button in ordered])


def parse_method_callback_data(data: str) -> str | None:
    """ "anilist"/"shikimori"/"manual", or None if `data` isn't a method
    pick."""
    if data == ANILIST_METHOD_CALLBACK_DATA:
        return "anilist"
    if data == SHIKIMORI_METHOD_CALLBACK_DATA:
        return "shikimori"
    if data == MANUAL_METHOD_CALLBACK_DATA:
        return "manual"
    return None
