"""Inline-keyboard building for the DM anime-identification flow — see
MECHANICS.md's "Starting a game" section.

Button labels are routed through `i18n.t()` like every other user-facing
string, with two deliberate exceptions: the method-picker and
screenshot-source brand labels (`Provider.display_name` — third-party
brand names, not translatable UI text) and the language picker's own
native-name labels in `commands/language.py` (a language switcher
inherently shows each option in its own name).

This module is also the trust boundary for provider identity. Every
`CallbackQueryHandler` in app.py matches on prefix only, so the rest of
a payload is whatever the client chose to send; the parsers below are
where a provider segment stops being an arbitrary string and becomes a
`Provider` (see `_validated_provider`), so nothing downstream has to
re-check it.

`stop_confirm_keyboard()` lives in `commands/helpers/keyboards.py`
instead — it's shared across this package and `commands/stageconfig.py`,
not specific to the setup flow.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.models.enums import DISCOURAGED_ALGORITHMS, PixelAlgorithm, Provider
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tenrai import TenraiResult
from nani_pix_bot.services.search.tmdb import TMDBResult

SEARCH_RETRY_CALLBACK_DATA = "search_retry"

# Kept as named module constants (not inlined like the pick-prefix values
# below) because several dm_start test files import these directly — but
# derived from Provider.method_callback_data, not an independent
# f"method:{...}" restatement, so the wire format still has one source of
# truth (issue #114).
ANILIST_METHOD_CALLBACK_DATA = Provider.ANILIST.method_callback_data
SHIKIMORI_METHOD_CALLBACK_DATA = Provider.SHIKIMORI.method_callback_data
TENRAI_METHOD_CALLBACK_DATA = Provider.TENRAI.method_callback_data
TMDB_METHOD_CALLBACK_DATA = Provider.TMDB.method_callback_data
# "manual" is deliberately not a Provider member (see its docstring) —
# stays a standalone literal.
MANUAL_METHOD_CALLBACK_DATA = "method:manual"

PREVIEW_CONFIRM_CALLBACK_DATA = "preview:confirm"
PREVIEW_CHANGE_IMAGE_CALLBACK_DATA = "preview:change_image"
# The two options offered when "Change image" is tapped on an
# API-sourced screenshot (see change_image_keyboard/ticket 9) — a
# genuine upload skips straight to AWAITING_PHOTO_CHANGE without
# either of these ever being shown.
PREVIEW_CHANGE_IMAGE_UPLOAD_CALLBACK_DATA = "preview:change_image:upload"
PREVIEW_CHANGE_IMAGE_PICK_SCREENSHOT_CALLBACK_DATA = "preview:change_image:pick_screenshot"
PREVIEW_RESEARCH_CALLBACK_DATA = "preview:research"
PREVIEW_ADD_SYNONYM_CALLBACK_DATA = "preview:add_synonym"
# The pixelation-algorithm submenu (see pixel_algorithm_keyboard). The
# pick prefix carries its own segment rather than being "preview:algo:"
# directly, so it can never collide with the ":back" button's data.
PREVIEW_PIXEL_ALGORITHM_CALLBACK_DATA = "preview:algo"
PREVIEW_PIXEL_ALGORITHM_BACK_CALLBACK_DATA = "preview:algo:back"
PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX = "preview:algo:pick:"


_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True)
class _ResultAccessors(Generic[_ResultT]):
    """A provider's own display-label rule and id field — bundled into
    one object so `_results_keyboard` doesn't need a 5-argument
    signature (a qlty "many parameters" smell) on top of its
    `results`/`lang`/`pick_prefix`."""

    label_fn: Callable[[_ResultT, str], str]
    id_fn: Callable[[_ResultT], int]


def _results_keyboard(
    results: list[_ResultT], lang: str, accessors: _ResultAccessors[_ResultT], pick_prefix: str
) -> InlineKeyboardMarkup:
    """Shared body behind every `*_results_keyboard` builder below — one
    button per result plus a trailing retry button. Where that retry
    lands follows from `pick_prefix` (see `_retry_data_for`), so the two
    can't drift apart the way they did while the retry was its own
    never-passed keyword."""
    buttons = [
        [
            InlineKeyboardButton(
                accessors.label_fn(result, lang),
                callback_data=f"{pick_prefix}{accessors.id_fn(result)}",
            )
        ]
        for result in results
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                i18n.t("keyboards.retry", lang), callback_data=_retry_data_for(pick_prefix)
            )
        ]
    )
    return InlineKeyboardMarkup(buttons)


def _retry_data_for(pick_prefix: str) -> str:
    """Where this keyboard's "None of these" button lands, derived from
    where its picks land.

    Identification search's own results retry into
    `pick_callback_handler`'s retry branch. The screenshot cross-search
    (the one caller that overrides `pick_prefix`, to
    "screenshot_search_pick:<provider>:") has to retry into *its* flow
    instead: it used to inherit identification's retry, which edited the
    message to "Okay, type a new search query." with no keyboard at all —
    cross-wired, and a buttonless prompt reached by accident.

    The prefixes below are defined further down in this module, with the
    rest of the screenshot sub-flow's callback data; they're read when
    this runs, not at import."""
    if not pick_prefix.startswith(SCREENSHOT_SEARCH_PICK_PREFIX):
        return SEARCH_RETRY_CALLBACK_DATA
    provider = pick_prefix.removeprefix(SCREENSHOT_SEARCH_PICK_PREFIX).rstrip(":")
    return f"{SCREENSHOT_SEARCH_AGAIN_PREFIX}{provider}"


def anilist_results_keyboard(results: list[AniListResult], lang: str) -> InlineKeyboardMarkup:
    accessors = _ResultAccessors(label_fn=_anilist_label, id_fn=lambda result: result.anilist_id)
    return _results_keyboard(results, lang, accessors, Provider.ANILIST.pick_prefix)


def shikimori_results_keyboard(
    results: list[ShikimoriResult], lang: str, *, pick_prefix: str = Provider.SHIKIMORI.pick_prefix
) -> InlineKeyboardMarkup:
    """`pick_prefix` is overridable so ticket 8's cross-provider "Wrong
    anime? Search again" flow can route its picks to a different handler
    (commands/dm_start/screenshot_gallery.py) than identification
    search's own pick_callback_handler."""
    accessors = _ResultAccessors(
        label_fn=_shikimori_label, id_fn=lambda result: result.shikimori_id
    )
    return _results_keyboard(results, lang, accessors, pick_prefix)


def tenrai_results_keyboard(
    results: list[TenraiResult], lang: str, *, pick_prefix: str = Provider.TENRAI.pick_prefix
) -> InlineKeyboardMarkup:
    """See `shikimori_results_keyboard` for why `pick_prefix` is
    overridable."""
    accessors = _ResultAccessors(label_fn=_tenrai_label, id_fn=lambda result: result.tenrai_id)
    return _results_keyboard(results, lang, accessors, pick_prefix)


def tmdb_results_keyboard(
    results: list[TMDBResult], lang: str, *, pick_prefix: str = Provider.TMDB.pick_prefix
) -> InlineKeyboardMarkup:
    """See `shikimori_results_keyboard` for why `pick_prefix` is
    overridable."""
    accessors = _ResultAccessors(label_fn=_tmdb_label, id_fn=lambda result: result.tmdb_id)
    return _results_keyboard(results, lang, accessors, pick_prefix)


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


def _tenrai_label(result: TenraiResult, lang: str) -> str:
    """Picks the result's display title via the same priority rule as
    `_anilist_label()` above. Tenrai doesn't expose a Russian-specific
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


def _validated_index(raw: str, *, data: str) -> int | None:
    """The trailing id/index segment of a callback payload as an int, or
    None if it isn't one.

    Every `CallbackQueryHandler` in app.py matches on **prefix** only,
    so everything after that prefix is whatever the client chose to put
    there — an MTProto client can send arbitrary `data` for a button.
    A bare `int()` on it raised ValueError out of the handler, which
    reached app's error handler as an unhandled exception and showed the
    starter nothing at all; every caller of these parsers already has a
    "not a pick" path for None, so rejection goes down that one.

    `.isdecimal()`, not `.isdigit()`: the latter is also True for
    characters that merely carry the Unicode *digit* property, several of
    which `int()` then refuses — `"²".isdigit()` is True and
    `int("²")` raises, and a superscript two is two bytes of valid
    UTF-8, well inside Telegram's callback-data cap. Guarding with
    `.isdigit()` would have left exactly the ValueError this function
    exists to prevent. ASCII is unaffected, and genuinely decimal
    non-ASCII digits (`"٢"`) still convert cleanly."""
    if raw.isdecimal():
        return int(raw)
    logger.warning("Rejected callback payload {!r}: {!r} is not an index", data, raw)
    return None


def parse_pick_callback_data(data: str) -> tuple[Provider, int] | None:
    """The picked result's (source, id), or None if `data` wasn't a pick
    (e.g. retry, or a client-forged id — see `_validated_index`). The
    command layer uses `source` to know which service's get_by_id to
    re-fetch from (restart-resilient, per issue #11). It comes from the
    prefix rather than the payload, so unlike the screenshot parsers
    below there is no provider segment here that could need validating —
    a prefix that doesn't match any member's `pick_prefix` simply isn't a
    pick."""
    for source in Provider:
        prefix = source.pick_prefix
        if data.startswith(prefix):
            external_id = _validated_index(data.removeprefix(prefix), data=data)
            return None if external_id is None else (source, external_id)
    return None


def method_selection_keyboard(*, prefer_shikimori: bool, lang: str) -> InlineKeyboardMarkup:
    """AniList vs. Shikimori vs. Tenrai vs. TMDB vs. manual-entry choice,
    shown right after the starter's photo (or, from /newgame, before
    any photo exists). Shikimori is offered first for RU-language bots
    (see MECHANICS.md's "Starting a game"); Tenrai/TMDB have no
    RU-specific reason to move around, so they're always third/fourth,
    before manual entry. "AniList"/"Shikimori"/"Tenrai"/"TMDB" are brand
    names and stay untranslated regardless of `lang`."""
    anilist_button = InlineKeyboardButton(
        Provider.ANILIST.display_name, callback_data=ANILIST_METHOD_CALLBACK_DATA
    )
    shikimori_button = InlineKeyboardButton(
        Provider.SHIKIMORI.display_name, callback_data=SHIKIMORI_METHOD_CALLBACK_DATA
    )
    tenrai_button = InlineKeyboardButton(
        Provider.TENRAI.display_name, callback_data=TENRAI_METHOD_CALLBACK_DATA
    )
    tmdb_button = InlineKeyboardButton(
        Provider.TMDB.display_name, callback_data=TMDB_METHOD_CALLBACK_DATA
    )
    manual_button = InlineKeyboardButton(
        i18n.t("keyboards.manual_entry", lang), callback_data=MANUAL_METHOD_CALLBACK_DATA
    )
    ordered = (
        [shikimori_button, anilist_button]
        if prefer_shikimori
        else [anilist_button, shikimori_button]
    )
    ordered.append(tenrai_button)
    ordered.append(tmdb_button)
    ordered.append(manual_button)
    return InlineKeyboardMarkup([[button] for button in ordered])


_METHOD_CALLBACK_DATA_TO_SOURCE: dict[str, Provider | Literal["manual"]] = {
    ANILIST_METHOD_CALLBACK_DATA: Provider.ANILIST,
    SHIKIMORI_METHOD_CALLBACK_DATA: Provider.SHIKIMORI,
    TENRAI_METHOD_CALLBACK_DATA: Provider.TENRAI,
    TMDB_METHOD_CALLBACK_DATA: Provider.TMDB,
    MANUAL_METHOD_CALLBACK_DATA: "manual",
}


def parse_method_callback_data(data: str) -> Provider | Literal["manual"] | None:
    """The identification method picked, or None if `data` isn't a method
    pick. The one parser whose result isn't purely a `Provider`: manual
    entry is the fifth button but not a fifth provider, so it stays the
    bare string `Game.source` has always stored for it (see `Provider`'s
    docstring)."""
    return _METHOD_CALLBACK_DATA_TO_SOURCE.get(data)


def algorithm_name(algorithm: PixelAlgorithm, lang: str) -> str:
    """The localized display name for a pixelation algorithm. Unlike the
    provider brand names, these are ordinary words and do get
    translated — see CLAUDE.md's i18n section on what doesn't."""
    return i18n.t(f"dm_start.algo_name_{algorithm.value}", lang)


def preview_keyboard(lang: str, algorithm: PixelAlgorithm) -> InlineKeyboardMarkup:
    """Buttons on the private preview shown before a game is posted to
    the group — see MECHANICS.md's "Starting a game" section. The
    pixelation button shows the game's current algorithm rather than a
    static label, so the starter can see what they'd be changing without
    opening the submenu."""
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
    pixel_algorithm = InlineKeyboardButton(
        i18n.t("keyboards.preview_pixel_algorithm", lang, name=algorithm_name(algorithm, lang)),
        callback_data=PREVIEW_PIXEL_ALGORITHM_CALLBACK_DATA,
    )
    return InlineKeyboardMarkup(
        [[confirm], [change_image], [research], [add_synonym], [pixel_algorithm]]
    )


def pixel_algorithm_keyboard(lang: str, current: PixelAlgorithm) -> InlineKeyboardMarkup:
    """The pixelation submenu: every algorithm, the current one ticked
    and the discouraged one flagged, plus a way back. Descriptions go in
    the message text rather than on the buttons — a one-line explanation
    per algorithm doesn't fit a button label at any sensible width."""
    rows = [
        [
            InlineKeyboardButton(
                _algorithm_button_label(algorithm, lang, current=current),
                callback_data=f"{PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX}{algorithm.value}",
            )
        ]
        for algorithm in PixelAlgorithm
    ]
    rows.append(
        [
            InlineKeyboardButton(
                i18n.t("keyboards.pixel_algorithm_back", lang),
                callback_data=PREVIEW_PIXEL_ALGORITHM_BACK_CALLBACK_DATA,
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _algorithm_button_label(
    algorithm: PixelAlgorithm, lang: str, *, current: PixelAlgorithm
) -> str:
    markers = []
    if algorithm is current:
        markers.append(i18n.t("dm_start.pixel_algorithm_current_marker", lang))
    if algorithm in DISCOURAGED_ALGORITHMS:
        markers.append(i18n.t("dm_start.pixel_algorithm_worst_marker", lang))
    name = algorithm_name(algorithm, lang)
    return f"{' '.join(markers)} {name}".strip()


def change_image_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Shown instead of going straight to AWAITING_PHOTO_CHANGE when the
    preview's "Change image" is tapped on an API-sourced screenshot
    (`Game.screenshot_source` set) — ticket 9's preview-screen
    branching. A genuine upload never sees this keyboard."""
    upload = InlineKeyboardButton(
        i18n.t("keyboards.change_image_upload", lang),
        callback_data=PREVIEW_CHANGE_IMAGE_UPLOAD_CALLBACK_DATA,
    )
    pick_screenshot = InlineKeyboardButton(
        i18n.t("keyboards.change_image_pick_screenshot", lang),
        callback_data=PREVIEW_CHANGE_IMAGE_PICK_SCREENSHOT_CALLBACK_DATA,
    )
    return InlineKeyboardMarkup([[upload], [pick_screenshot]])


# --- Screenshot-source selection + gallery (the screenshot-less
# /newgame flow's own sub-flow — see commands/dm_start/screenshots.py
# and screenshot_gallery.py) ---

SCREENSHOT_SOURCE_PREFIX = "screenshot_source:"
SCREENSHOT_PICK_PREFIX = "screenshot_pick:"
SCREENSHOT_MORE_PREFIX = "screenshot_more:"
SCREENSHOT_SEARCH_AGAIN_PREFIX = "screenshot_search_again:"
# A cross-provider-resolution search's own pick, format
# "screenshot_search_pick:<provider>:<id>" — distinct from the plain
# "<provider>_pick:<id>" identification-search prefixes above so its
# taps route to screenshot_gallery.py's own handler instead of
# search.py's pick_callback_handler (which would wrongly re-stage
# identification fields via stage_result() — see
# game_service.set_screenshot_provider_id).
SCREENSHOT_SEARCH_PICK_PREFIX = "screenshot_search_pick:"
SCREENSHOT_UPLOAD_CALLBACK_DATA = "screenshot:upload"

# Marks the provider that just failed on a re-shown source menu. A bare
# sign rather than an i18n'd word so the brand-name labels stay
# untranslated (see CLAUDE.md) and the buttons stay short.
_FAILED_PROVIDER_MARK = "⚠️"


def _source_label(provider: Provider, *, failed: bool) -> str:
    label = provider.display_name
    return f"{_FAILED_PROVIDER_MARK} {label}" if failed else label


def screenshot_source_keyboard(
    providers: list[Provider], lang: str, *, failed_provider: Provider | None = None
) -> InlineKeyboardMarkup:
    """One button per screenshot-capable provider in `providers`
    (already ordered by the caller — same provider as identification
    first), plus "Upload my own instead".

    `failed_provider` prefixes that one provider's label with a warning
    sign — this keyboard is re-shown as the escape hatch from every
    screenshot-sub-flow failure (see screenshots.py's
    `reply_with_source_menu`), and the starter shouldn't have to
    remember which source just let them down. It stays tappable on
    purpose: provider outages are usually transient, and it may be the
    only source with screenshots for this title.

    The labels themselves are third-party brand names, deliberately not
    translated — see CLAUDE.md's i18n notes."""
    buttons = [
        [
            InlineKeyboardButton(
                _source_label(provider, failed=provider == failed_provider),
                # Interpolates as the value, not "Provider.TMDB" — that
                # is what StrEnum guarantees and why it was chosen (see
                # Provider's docstring); same for every other callback
                # payload built from a member in this module.
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


def _validated_provider(raw: str, *, data: str) -> Provider | None:
    """`raw` as a screenshot-capable `Provider`, or None.

    Same reasoning as `_validated_index`: the provider segment of a
    callback payload is client-controlled, and an unknown string flowed
    straight into `Provider(raw).id_attr_name` / `.screenshot_module` /
    a display-name lookup and raised an error a few frames later —
    including once it had already been written to
    `screenshot_picker_provider`.

    Two checks, not one. `Provider(raw)` rejects anything that isn't a
    provider at all (and is what makes this the point where forged data
    becomes a typed value for everything downstream); the membership test
    then rejects a real Provider that has no screenshots to offer —
    `anilist`, the only one. The dict-key lookup this replaces did both
    at once only because the label dict happened to omit AniList."""
    try:
        provider = Provider(raw)
    except ValueError:
        logger.warning("Rejected callback payload {!r}: {!r} is not a provider", data, raw)
        return None
    if provider not in game_service.SCREENSHOT_CAPABLE_PROVIDERS:
        logger.warning(
            "Rejected callback payload {!r}: {!r} has no screenshots to offer", data, raw
        )
        return None
    return provider


def _parse_provider_and_index(payload: str, *, data: str) -> tuple[Provider, int] | None:
    """Splits a "<provider>:<number>" callback suffix and validates both
    halves — the shared shape behind every screenshot pick/page/search
    pick payload. None if either half doesn't hold up."""
    raw_provider, _, raw_index = payload.partition(":")
    provider = _validated_provider(raw_provider, data=data)
    index = _validated_index(raw_index, data=data)
    if provider is None or index is None:
        return None
    return provider, index


def parse_screenshot_source_callback_data(data: str) -> Provider | None:
    if not data.startswith(SCREENSHOT_SOURCE_PREFIX):
        return None
    return _validated_provider(data.removeprefix(SCREENSHOT_SOURCE_PREFIX), data=data)


@dataclass(frozen=True)
class GalleryPage:
    """What the gallery keyboard needs to render one page: which
    provider/offset/count of screenshots are shown, whether there's
    another page, and whether this gallery came from a cross-provider
    resolution (see ticket 8) — bundled into one object so
    `screenshot_gallery_keyboard` doesn't need a 6-argument signature
    (a qlty "many parameters" smell)."""

    provider: Provider
    offset: int
    count: int
    has_more: bool
    cross_provider: bool
    # Offset of the page to go back to, or None on the first page. The
    # caller computes it because the page size lives in screenshots.py,
    # which already imports this module — importing it back would be a
    # cycle.
    previous_offset: int | None = None


def _gallery_paging_row(page: GalleryPage, lang: str) -> list[InlineKeyboardButton]:
    """Back and/or forward, sharing one row — either can be absent (the
    first page has no back, the last has no forward), and on a gallery
    that fits in a single page the row is empty and gets dropped.

    Both use the same SCREENSHOT_MORE_PREFIX callback: it has always
    encoded "show the page at this offset" rather than a direction, so
    paging backwards needs no new handler, and the provider's url list
    is cached (see services/search/cache.py) so it costs no extra API
    call either."""
    row = []
    if page.previous_offset is not None:
        row.append(
            InlineKeyboardButton(
                i18n.t("keyboards.previous_screenshots", lang),
                callback_data=f"{SCREENSHOT_MORE_PREFIX}{page.provider}:{page.previous_offset}",
            )
        )
    if page.has_more:
        row.append(
            InlineKeyboardButton(
                i18n.t("keyboards.more_screenshots", lang),
                callback_data=f"{SCREENSHOT_MORE_PREFIX}{page.provider}:{page.offset + page.count}",
            )
        )
    return row


def screenshot_gallery_keyboard(page: GalleryPage, lang: str) -> InlineKeyboardMarkup:
    """Numbered buttons for the `page.count` screenshots currently shown
    (absolute indices `page.offset`..`page.offset + page.count - 1`),
    a back/forward paging row, "Wrong anime? Search again" (only if
    `page.cross_provider` — this gallery came from a cross-provider
    resolution, see ticket 8), and "Upload my own instead".

    The number labels are **absolute** positions, matching the captions
    `_show_gallery_page` puts on the album photos themselves — they used
    to restart at 1 on every page, so page 2 offered buttons labelled
    1,2,3 under photos captioned 6,7,8. Only the labels were wrong (the
    callback data has always carried the absolute index), but it told
    the starter the wrong thing."""
    number_row = [
        InlineKeyboardButton(
            str(page.offset + i + 1),
            callback_data=f"{SCREENSHOT_PICK_PREFIX}{page.provider}:{page.offset + i}",
        )
        for i in range(page.count)
    ]
    rows = [number_row]
    paging_row = _gallery_paging_row(page, lang)
    if paging_row:
        rows.append(paging_row)
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


def parse_screenshot_pick_callback_data(data: str) -> tuple[Provider, int] | None:
    if not data.startswith(SCREENSHOT_PICK_PREFIX):
        return None
    return _parse_provider_and_index(data.removeprefix(SCREENSHOT_PICK_PREFIX), data=data)


def parse_screenshot_more_callback_data(data: str) -> tuple[Provider, int] | None:
    if not data.startswith(SCREENSHOT_MORE_PREFIX):
        return None
    return _parse_provider_and_index(data.removeprefix(SCREENSHOT_MORE_PREFIX), data=data)


def parse_screenshot_search_again_callback_data(data: str) -> Provider | None:
    if not data.startswith(SCREENSHOT_SEARCH_AGAIN_PREFIX):
        return None
    return _validated_provider(data.removeprefix(SCREENSHOT_SEARCH_AGAIN_PREFIX), data=data)


def parse_screenshot_search_pick_callback_data(data: str) -> tuple[Provider, int] | None:
    if not data.startswith(SCREENSHOT_SEARCH_PICK_PREFIX):
        return None
    return _parse_provider_and_index(data.removeprefix(SCREENSHOT_SEARCH_PICK_PREFIX), data=data)
