"""Inline-keyboard building for the DM anime-identification flow — see
MECHANICS.md's "Starting a game" section.

Button labels are routed through `i18n.t()` like every other user-facing
string, with two deliberate exceptions: the "AniList"/"Shikimori"
method-picker labels (third-party brand names, not translatable UI text)
and the language picker's own native-name labels in `commands/language.py`
(a language switcher inherently shows each option in its own name).

`stop_confirm_keyboard()` lives in `commands/helpers/keyboards.py`
instead — it's shared across this package and `commands/stageconfig.py`,
not specific to the setup flow.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.services import i18n
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult

RETRY_CALLBACK_DATA = "anilist_retry"
_ANILIST_PICK_PREFIX = "anilist_pick:"
_SHIKIMORI_PICK_PREFIX = "shikimori_pick:"

ANILIST_METHOD_CALLBACK_DATA = "method:anilist"
SHIKIMORI_METHOD_CALLBACK_DATA = "method:shikimori"
MANUAL_METHOD_CALLBACK_DATA = "method:manual"

PREVIEW_CONFIRM_CALLBACK_DATA = "preview:confirm"
PREVIEW_CHANGE_IMAGE_CALLBACK_DATA = "preview:change_image"
PREVIEW_RESEARCH_CALLBACK_DATA = "preview:research"
PREVIEW_ADD_SYNONYM_CALLBACK_DATA = "preview:add_synonym"


def anilist_results_keyboard(results: list[AniListResult], lang: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                _anilist_label(result, lang),
                callback_data=f"{_ANILIST_PICK_PREFIX}{result.anilist_id}",
            )
        ]
        for result in results
    ]
    buttons.append(
        [InlineKeyboardButton(i18n.t("keyboards.retry", lang), callback_data=RETRY_CALLBACK_DATA)]
    )
    return InlineKeyboardMarkup(buttons)


def shikimori_results_keyboard(results: list[ShikimoriResult], lang: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                _shikimori_label(result, lang),
                callback_data=f"{_SHIKIMORI_PICK_PREFIX}{result.shikimori_id}",
            )
        ]
        for result in results
    ]
    buttons.append(
        [InlineKeyboardButton(i18n.t("keyboards.retry", lang), callback_data=RETRY_CALLBACK_DATA)]
    )
    return InlineKeyboardMarkup(buttons)


def _anilist_label(result: AniListResult, lang: str) -> str:
    """Picks the result's display title, preferring whatever matches
    `lang` first, then falling back through the rest. AniList doesn't
    expose a Russian-specific field — `lang` is accepted for symmetry
    with `_shikimori_label()` and in case that ever changes, but every
    branch currently resolves the same way (English, then romaji, then
    native)."""
    del lang
    title = result.title_english or result.title_romaji or result.title_native or "?"
    return f"{title} ({result.year})" if result.year else title


def _shikimori_label(result: ShikimoriResult, lang: str) -> str:
    """Picks the result's display title, preferring the Russian title
    only when the bot's language is RU — otherwise English/romaji comes
    first, with the Russian title as a last resort rather than always
    winning regardless of the bot's language."""
    if lang.upper() == "RU":
        candidates = (result.title_russian, result.title_english, result.title_romaji)
    else:
        candidates = (result.title_english, result.title_romaji, result.title_russian)
    return next((title for title in candidates if title), "?")


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


def method_selection_keyboard(*, prefer_shikimori: bool, lang: str) -> InlineKeyboardMarkup:
    """AniList vs. Shikimori vs. manual-entry choice, shown right after
    the starter's photo. Shikimori is offered first for RU-language bots
    (see MECHANICS.md's "Starting a game"). "AniList"/"Shikimori" are
    brand names and stay untranslated regardless of `lang`."""
    anilist_button = InlineKeyboardButton("AniList", callback_data=ANILIST_METHOD_CALLBACK_DATA)
    shikimori_button = InlineKeyboardButton(
        "Shikimori", callback_data=SHIKIMORI_METHOD_CALLBACK_DATA
    )
    manual_button = InlineKeyboardButton(
        i18n.t("keyboards.manual_entry", lang), callback_data=MANUAL_METHOD_CALLBACK_DATA
    )
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


def preview_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Buttons on the private preview shown before a game is posted to
    the group — see MECHANICS.md's "Starting a game" section."""
    confirm = InlineKeyboardButton(
        i18n.t("keyboards.preview_confirm", lang), callback_data=PREVIEW_CONFIRM_CALLBACK_DATA
    )
    change_image = InlineKeyboardButton(
        i18n.t("keyboards.preview_change_image", lang),
        callback_data=PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
    )
    research = InlineKeyboardButton(
        i18n.t("keyboards.preview_research", lang), callback_data=PREVIEW_RESEARCH_CALLBACK_DATA
    )
    add_synonym = InlineKeyboardButton(
        i18n.t("keyboards.preview_add_synonym", lang),
        callback_data=PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    )
    return InlineKeyboardMarkup([[confirm], [change_image], [research], [add_synonym]])
