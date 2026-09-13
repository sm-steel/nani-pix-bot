"""Inline-keyboard building for the DM anime-identification flow — see
MECHANICS.md's "Starting a game" section.

Button labels are routed through `i18n.t()` like every other user-facing
string, with two deliberate exceptions: the "AniList"/"Shikimori"/
"Jikan"/"TMDB" method-picker labels (third-party brand names, not
translatable UI text) and the language picker's own native-name labels
in `commands/language.py` (a language switcher inherently shows each
option in its own name).

`stop_confirm_keyboard()` lives in `commands/helpers/keyboards.py`
instead — it's shared across this package and `commands/stageconfig.py`,
not specific to the setup flow.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tmdb import TMDBResult

RETRY_CALLBACK_DATA = "anilist_retry"
_ANILIST_PICK_PREFIX = "anilist_pick:"
_SHIKIMORI_PICK_PREFIX = "shikimori_pick:"
_JIKAN_PICK_PREFIX = "jikan_pick:"
_TMDB_PICK_PREFIX = "tmdb_pick:"

ANILIST_METHOD_CALLBACK_DATA = "method:anilist"
SHIKIMORI_METHOD_CALLBACK_DATA = "method:shikimori"
JIKAN_METHOD_CALLBACK_DATA = "method:jikan"
TMDB_METHOD_CALLBACK_DATA = "method:tmdb"
MANUAL_METHOD_CALLBACK_DATA = "method:manual"

PREVIEW_CONFIRM_CALLBACK_DATA = "preview:confirm"
PREVIEW_CHANGE_IMAGE_CALLBACK_DATA = "preview:change_image"
PREVIEW_RESEARCH_CALLBACK_DATA = "preview:research"
PREVIEW_ADD_SYNONYM_CALLBACK_DATA = "preview:add_synonym"


_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True)
class _ResultAccessors(Generic[_ResultT]):
    """A provider's own display-label rule and id field — bundled with
    `_CallbackRouting` below so `_results_keyboard` doesn't need a
    5-argument signature (a qlty "many parameters" smell) on top of its
    `results`/`lang`."""

    label_fn: Callable[[_ResultT, str], str]
    id_fn: Callable[[_ResultT], int]


@dataclass(frozen=True)
class _CallbackRouting:
    """Defaults to identification search's own callback data on every
    public `*_results_keyboard` wrapper, but ticket 8's cross-provider
    "Wrong anime? Search again" flow overrides both to route its picks
    to a different handler (commands/dm_start/screenshots.py) than
    identification search's own pick_callback_handler."""

    pick_prefix: str
    retry_data: str = RETRY_CALLBACK_DATA


def _results_keyboard(
    results: list[_ResultT],
    lang: str,
    accessors: _ResultAccessors[_ResultT],
    routing: _CallbackRouting,
) -> InlineKeyboardMarkup:
    """Shared body behind every `*_results_keyboard` builder below — one
    button per result plus a trailing retry button."""
    buttons = [
        [
            InlineKeyboardButton(
                accessors.label_fn(result, lang),
                callback_data=f"{routing.pick_prefix}{accessors.id_fn(result)}",
            )
        ]
        for result in results
    ]
    buttons.append(
        [InlineKeyboardButton(i18n.t("keyboards.retry", lang), callback_data=routing.retry_data)]
    )
    return InlineKeyboardMarkup(buttons)


def anilist_results_keyboard(results: list[AniListResult], lang: str) -> InlineKeyboardMarkup:
    accessors = _ResultAccessors(label_fn=_anilist_label, id_fn=lambda result: result.anilist_id)
    return _results_keyboard(results, lang, accessors, _CallbackRouting(_ANILIST_PICK_PREFIX))


def shikimori_results_keyboard(
    results: list[ShikimoriResult],
    lang: str,
    *,
    pick_prefix: str = _SHIKIMORI_PICK_PREFIX,
    retry_data: str = RETRY_CALLBACK_DATA,
) -> InlineKeyboardMarkup:
    """See _CallbackRouting's docstring for why `pick_prefix`/`retry_data`
    are overridable."""
    accessors = _ResultAccessors(
        label_fn=_shikimori_label, id_fn=lambda result: result.shikimori_id
    )
    return _results_keyboard(results, lang, accessors, _CallbackRouting(pick_prefix, retry_data))


def jikan_results_keyboard(
    results: list[JikanResult],
    lang: str,
    *,
    pick_prefix: str = _JIKAN_PICK_PREFIX,
    retry_data: str = RETRY_CALLBACK_DATA,
) -> InlineKeyboardMarkup:
    """See _CallbackRouting's docstring for why `pick_prefix`/`retry_data`
    are overridable."""
    accessors = _ResultAccessors(label_fn=_jikan_label, id_fn=lambda result: result.jikan_id)
    return _results_keyboard(results, lang, accessors, _CallbackRouting(pick_prefix, retry_data))


def tmdb_results_keyboard(
    results: list[TMDBResult],
    lang: str,
    *,
    pick_prefix: str = _TMDB_PICK_PREFIX,
    retry_data: str = RETRY_CALLBACK_DATA,
) -> InlineKeyboardMarkup:
    """See _CallbackRouting's docstring for why `pick_prefix`/`retry_data`
    are overridable."""
    accessors = _ResultAccessors(label_fn=_tmdb_label, id_fn=lambda result: result.tmdb_id)
    return _results_keyboard(results, lang, accessors, _CallbackRouting(pick_prefix, retry_data))


def _anilist_label(result: AniListResult, lang: str) -> str:
    """Picks the result's display title via the same priority rule
    display_title() uses for the confirmation preview and every caption
    (services/game/state.py's prioritized_title()) — so the button a
    starter taps always shows the same title the rest of the game will
    call this pick. AniList doesn't expose a Russian-specific field, so
    every branch currently resolves the same way (English, then romaji,
    then native) regardless of `lang`."""
    variants = game_service.TitleVariants(
        english=result.title_english, romaji=result.title_romaji, native=result.title_native
    )
    title = game_service.prioritized_title(variants, lang=lang)
    return f"{title} ({result.year})" if result.year else title


def _shikimori_label(result: ShikimoriResult, lang: str) -> str:
    """Picks the result's display title via the same priority rule as
    `_anilist_label()` above — preferring the Russian title only when
    the bot's language is RU, otherwise English/romaji first. This must
    stay the exact rule prioritized_title()/display_title() use, or the
    button a starter taps here can show a different title than the
    confirmation preview ends up calling that same pick (see issue #50's
    "Grand Blue"/"Grand Blue Dreaming" mismatch)."""
    variants = game_service.TitleVariants(
        english=result.title_english, romaji=result.title_romaji, russian=result.title_russian
    )
    return game_service.prioritized_title(variants, lang=lang)


def _jikan_label(result: JikanResult, lang: str) -> str:
    """Picks the result's display title via the same priority rule as
    `_anilist_label()` above. Jikan doesn't expose a Russian-specific
    field either, so every branch resolves the same way (English, then
    romaji, then native) regardless of `lang`."""
    variants = game_service.TitleVariants(
        english=result.title_english, romaji=result.title_romaji, native=result.title_native
    )
    return game_service.prioritized_title(variants, lang=lang)


def _tmdb_label(result: TMDBResult, lang: str) -> str:
    """Picks the result's display title via the same priority rule as
    `_anilist_label()` above. TMDB has no distinct romaji/Russian
    fields, so every branch resolves the same way (English, then
    native) regardless of `lang`."""
    variants = game_service.TitleVariants(english=result.title_english, native=result.title_native)
    return game_service.prioritized_title(variants, lang=lang)


def parse_pick_callback_data(data: str) -> tuple[str, int] | None:
    """The picked result's (source, id), or None if `data` wasn't a pick
    (e.g. retry). `source` is "anilist"/"shikimori"/"jikan"/"tmdb" — the
    command layer uses it to know which service's get_by_id to re-fetch
    from (restart-resilient, per issue #11)."""
    if data.startswith(_ANILIST_PICK_PREFIX):
        return "anilist", int(data.removeprefix(_ANILIST_PICK_PREFIX))
    if data.startswith(_SHIKIMORI_PICK_PREFIX):
        return "shikimori", int(data.removeprefix(_SHIKIMORI_PICK_PREFIX))
    if data.startswith(_JIKAN_PICK_PREFIX):
        return "jikan", int(data.removeprefix(_JIKAN_PICK_PREFIX))
    if data.startswith(_TMDB_PICK_PREFIX):
        return "tmdb", int(data.removeprefix(_TMDB_PICK_PREFIX))
    return None


def method_selection_keyboard(*, prefer_shikimori: bool, lang: str) -> InlineKeyboardMarkup:
    """AniList vs. Shikimori vs. Jikan vs. TMDB vs. manual-entry choice,
    shown right after the starter's photo (or, from /newgame, before
    any photo exists). Shikimori is offered first for RU-language bots
    (see MECHANICS.md's "Starting a game"); Jikan/TMDB have no
    RU-specific reason to move around, so they're always third/fourth,
    before manual entry. "AniList"/"Shikimori"/"Jikan"/"TMDB" are brand
    names and stay untranslated regardless of `lang`."""
    anilist_button = InlineKeyboardButton("AniList", callback_data=ANILIST_METHOD_CALLBACK_DATA)
    shikimori_button = InlineKeyboardButton(
        "Shikimori", callback_data=SHIKIMORI_METHOD_CALLBACK_DATA
    )
    jikan_button = InlineKeyboardButton("Jikan", callback_data=JIKAN_METHOD_CALLBACK_DATA)
    tmdb_button = InlineKeyboardButton("TMDB", callback_data=TMDB_METHOD_CALLBACK_DATA)
    manual_button = InlineKeyboardButton(
        i18n.t("keyboards.manual_entry", lang), callback_data=MANUAL_METHOD_CALLBACK_DATA
    )
    ordered = (
        [shikimori_button, anilist_button]
        if prefer_shikimori
        else [anilist_button, shikimori_button]
    )
    ordered.append(jikan_button)
    ordered.append(tmdb_button)
    ordered.append(manual_button)
    return InlineKeyboardMarkup([[button] for button in ordered])


_METHOD_CALLBACK_DATA_TO_SOURCE = {
    ANILIST_METHOD_CALLBACK_DATA: "anilist",
    SHIKIMORI_METHOD_CALLBACK_DATA: "shikimori",
    JIKAN_METHOD_CALLBACK_DATA: "jikan",
    TMDB_METHOD_CALLBACK_DATA: "tmdb",
    MANUAL_METHOD_CALLBACK_DATA: "manual",
}


def parse_method_callback_data(data: str) -> str | None:
    """ "anilist"/"shikimori"/"jikan"/"tmdb"/"manual", or None if `data`
    isn't a method pick."""
    return _METHOD_CALLBACK_DATA_TO_SOURCE.get(data)


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


# --- Screenshot-source selection + gallery (the screenshot-less
# /newgame flow's own sub-flow — see commands/dm_start/screenshots.py) ---

SCREENSHOT_SOURCE_PREFIX = "screenshot_source:"
SCREENSHOT_PICK_PREFIX = "screenshot_pick:"
SCREENSHOT_MORE_PREFIX = "screenshot_more:"
SCREENSHOT_SEARCH_AGAIN_PREFIX = "screenshot_search_again:"
# A cross-provider-resolution search's own pick, format
# "screenshot_search_pick:<provider>:<id>" — distinct from the plain
# "<provider>_pick:<id>" identification-search prefixes above so its
# taps route to screenshots.py's own handler instead of search.py's
# pick_callback_handler (which would wrongly re-stage identification
# fields via stage_result() — see game_service.set_screenshot_provider_id).
SCREENSHOT_SEARCH_PICK_PREFIX = "screenshot_search_pick:"
SCREENSHOT_UPLOAD_CALLBACK_DATA = "screenshot:upload"

# Brand names, same untranslated-label convention as the method-picker
# buttons above.
_SCREENSHOT_PROVIDER_LABELS = {"shikimori": "Shikimori", "jikan": "Jikan", "tmdb": "TMDB"}


def screenshot_source_keyboard(providers: list[str], lang: str) -> InlineKeyboardMarkup:
    """One button per screenshot-capable provider in `providers`
    (already ordered by the caller — same provider as identification
    first), plus "Upload my own instead"."""
    buttons = [
        [
            InlineKeyboardButton(
                _SCREENSHOT_PROVIDER_LABELS[provider],
                callback_data=f"{SCREENSHOT_SOURCE_PREFIX}{provider}",
            )
        ]
        for provider in providers
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                i18n.t("keyboards.upload_own_instead", lang),
                callback_data=SCREENSHOT_UPLOAD_CALLBACK_DATA,
            )
        ]
    )
    return InlineKeyboardMarkup(buttons)


def parse_screenshot_source_callback_data(data: str) -> str | None:
    if data.startswith(SCREENSHOT_SOURCE_PREFIX):
        return data.removeprefix(SCREENSHOT_SOURCE_PREFIX)
    return None


@dataclass(frozen=True)
class GalleryPage:
    """What the gallery keyboard needs to render one page: which
    provider/offset/count of screenshots are shown, whether there's
    another page, and whether this gallery came from a cross-provider
    resolution (see ticket 8) — bundled into one object so
    `screenshot_gallery_keyboard` doesn't need a 6-argument signature
    (a qlty "many parameters" smell)."""

    provider: str
    offset: int
    count: int
    has_more: bool
    cross_provider: bool


def screenshot_gallery_keyboard(page: GalleryPage, lang: str) -> InlineKeyboardMarkup:
    """Numbered buttons for the `page.count` screenshots currently shown
    (absolute indices `page.offset`..`page.offset + page.count - 1`),
    plus "More screenshots" (only if `page.has_more`), "Wrong anime?
    Search again" (only if `page.cross_provider` — this gallery came
    from a cross-provider resolution, see ticket 8), and "Upload my own
    instead"."""
    number_row = [
        InlineKeyboardButton(
            str(i + 1),
            callback_data=f"{SCREENSHOT_PICK_PREFIX}{page.provider}:{page.offset + i}",
        )
        for i in range(page.count)
    ]
    rows = [number_row]
    if page.has_more:
        rows.append(
            [
                InlineKeyboardButton(
                    i18n.t("keyboards.more_screenshots", lang),
                    callback_data=(
                        f"{SCREENSHOT_MORE_PREFIX}{page.provider}:{page.offset + page.count}"
                    ),
                )
            ]
        )
    if page.cross_provider:
        rows.append(
            [
                InlineKeyboardButton(
                    i18n.t("keyboards.wrong_anime_search_again", lang),
                    callback_data=f"{SCREENSHOT_SEARCH_AGAIN_PREFIX}{page.provider}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                i18n.t("keyboards.upload_own_instead", lang),
                callback_data=SCREENSHOT_UPLOAD_CALLBACK_DATA,
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def parse_screenshot_pick_callback_data(data: str) -> tuple[str, int] | None:
    if not data.startswith(SCREENSHOT_PICK_PREFIX):
        return None
    provider, _, index = data.removeprefix(SCREENSHOT_PICK_PREFIX).partition(":")
    return provider, int(index)


def parse_screenshot_more_callback_data(data: str) -> tuple[str, int] | None:
    if not data.startswith(SCREENSHOT_MORE_PREFIX):
        return None
    provider, _, offset = data.removeprefix(SCREENSHOT_MORE_PREFIX).partition(":")
    return provider, int(offset)


def parse_screenshot_search_again_callback_data(data: str) -> str | None:
    if data.startswith(SCREENSHOT_SEARCH_AGAIN_PREFIX):
        return data.removeprefix(SCREENSHOT_SEARCH_AGAIN_PREFIX)
    return None


def parse_screenshot_search_pick_callback_data(data: str) -> tuple[str, int] | None:
    if not data.startswith(SCREENSHOT_SEARCH_PICK_PREFIX):
        return None
    provider, _, id_str = data.removeprefix(SCREENSHOT_SEARCH_PICK_PREFIX).partition(":")
    if not id_str.isdigit():
        return None
    return provider, int(id_str)
