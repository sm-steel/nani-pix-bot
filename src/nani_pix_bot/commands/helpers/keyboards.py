"""Inline-keyboard building for the DM AniList picker — see
MECHANICS.md's "Starting a game" section."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.services.anilist import AniListResult

RETRY_CALLBACK_DATA = "anilist_retry"
_PICK_PREFIX = "anilist_pick:"


def anilist_results_keyboard(results: list[AniListResult]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(_label(result), callback_data=f"{_PICK_PREFIX}{result.anilist_id}")]
        for result in results
    ]
    buttons.append(
        [InlineKeyboardButton("None of these — search again", callback_data=RETRY_CALLBACK_DATA)]
    )
    return InlineKeyboardMarkup(buttons)


def _label(result: AniListResult) -> str:
    title = result.title_english or result.title_romaji or result.title_native or "?"
    return f"{title} ({result.year})" if result.year else title


def parse_pick_callback_data(data: str) -> int | None:
    """The picked anilist_id, or None if `data` wasn't a pick (e.g. retry)."""
    if not data.startswith(_PICK_PREFIX):
        return None
    return int(data.removeprefix(_PICK_PREFIX))
