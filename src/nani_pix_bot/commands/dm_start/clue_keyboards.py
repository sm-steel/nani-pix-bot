"""Inline keyboards of the setup preview's clue screenshot panel (issue
#452) and of the gallery that adds to the author's list from a source —
split out of keyboards.py, whose total complexity they pushed past qlty's
threshold. Same trust boundary: the parsers here are where a client-sent
payload becomes a typed value (see keyboards.py's module docstring)."""

from dataclasses import dataclass

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.commands.dm_start.keyboards import (
    CLUE_PANEL_OPEN_CALLBACK_DATA,
    _parse_provider_and_index,
    _validated_index,
    _validated_provider,
    clue_mode_name,
)
from nani_pix_bot.models.clue_author_screenshot import ClueAuthorScreenshot
from nani_pix_bot.models.enums import ClueScreenshotMode, Provider
from nani_pix_bot.services import i18n

# The panel's buttons, all handled by commands/dm_start/clue_panel.py.
CLUE_PANEL_MODE_PREFIX = "cluepanel:mode:"
CLUE_PANEL_UPLOAD_CALLBACK_DATA = "cluepanel:upload"
CLUE_PANEL_REMOVE_CALLBACK_DATA = "cluepanel:remove"
CLUE_PANEL_REMOVE_PREFIX = "cluepanel:remove:"
CLUE_PANEL_BACK_CALLBACK_DATA = "cluepanel:back"
CLUE_PANEL_SOURCE_CALLBACK_DATA = "cluepanel:source"
# "Start anyway" on Confirm's empty-list warning (issue #453).
CLUE_PANEL_START_RANDOM_CALLBACK_DATA = "cluepanel:start_random"
# The gallery that adds to the author's list from a source, handled by
# commands/dm_start/clue_gallery.py: a source's first page, a later page
# (by its offset), and a pick (by the screenshot's index in the source's
# own list).
CLUE_GALLERY_SOURCE_PREFIX = "cluegal:src:"
CLUE_GALLERY_MORE_PREFIX = "cluegal:more:"
CLUE_GALLERY_PICK_PREFIX = "cluegal:pick:"


def _clue_back_row(lang: str) -> list[InlineKeyboardButton]:
    """Back to the clue panel, from its sub-screens."""
    return [
        InlineKeyboardButton(
            i18n.t("keyboards.clue_back", lang), callback_data=CLUE_PANEL_OPEN_CALLBACK_DATA
        )
    ]


def clue_panel_keyboard(lang: str, mode: ClueScreenshotMode, count: int) -> InlineKeyboardMarkup:
    """The clue screenshot panel: the three modes (the current one ticked),
    the ways to add to the author's list, Remove… while it has entries, and
    Back to the preview buttons."""
    modes = [
        InlineKeyboardButton(
            f"{'✅ ' if option == mode else ''}{clue_mode_name(option, lang)}",
            callback_data=f"{CLUE_PANEL_MODE_PREFIX}{option.value}",
        )
        for option in ClueScreenshotMode
    ]
    rows = [
        modes,
        [
            InlineKeyboardButton(
                i18n.t("keyboards.clue_add_from_source", lang),
                callback_data=CLUE_PANEL_SOURCE_CALLBACK_DATA,
            )
        ],
        [
            InlineKeyboardButton(
                i18n.t("keyboards.clue_upload", lang),
                callback_data=CLUE_PANEL_UPLOAD_CALLBACK_DATA,
            )
        ],
    ]
    if count:
        rows.append(
            [
                InlineKeyboardButton(
                    i18n.t("keyboards.clue_remove", lang),
                    callback_data=CLUE_PANEL_REMOVE_CALLBACK_DATA,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                i18n.t("keyboards.clue_back", lang), callback_data=CLUE_PANEL_BACK_CALLBACK_DATA
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def clue_back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([_clue_back_row(lang)])


def clue_remove_keyboard(lang: str, entries: list[ClueAuthorScreenshot]) -> InlineKeyboardMarkup:
    """One button per entry of the author's list, numbered in list order
    and named by where it came from, then Back to the panel."""
    rows = []
    for number, entry in enumerate(entries, start=1):
        source = (
            Provider(entry.source).display_name
            if entry.source
            else i18n.t("dm_start.clue_upload_label", lang)
        )
        label = i18n.t("keyboards.clue_remove_entry", lang, number=number, source=source)
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"{CLUE_PANEL_REMOVE_PREFIX}{entry.id}")]
        )
    rows.append(_clue_back_row(lang))
    return InlineKeyboardMarkup(rows)


def clue_source_keyboard(lang: str, providers: list[Provider]) -> InlineKeyboardMarkup:
    """The sources a clue screenshot can be picked from, then Back."""
    rows = [
        [
            InlineKeyboardButton(
                provider.display_name, callback_data=f"{CLUE_GALLERY_SOURCE_PREFIX}{provider}"
            )
        ]
        for provider in providers
    ]
    rows.append(_clue_back_row(lang))
    return InlineKeyboardMarkup(rows)


@dataclass(frozen=True)
class ClueGalleryPage:
    """One page of the clue gallery: `indices` are the shown screenshots'
    positions in the source's own list (what a pick names), numbered from
    `offset + 1` (their position among the screenshots on offer)."""

    provider: Provider
    offset: int
    indices: tuple[int, ...]
    has_more: bool


def clue_gallery_keyboard(page: ClueGalleryPage, lang: str) -> InlineKeyboardMarkup:
    numbers = [
        InlineKeyboardButton(
            str(page.offset + number),
            callback_data=f"{CLUE_GALLERY_PICK_PREFIX}{page.provider}:{index}",
        )
        for number, index in enumerate(page.indices, start=1)
    ]
    rows = [numbers]
    if page.has_more:
        more = page.offset + len(page.indices)
        rows.append(
            [
                InlineKeyboardButton(
                    i18n.t("keyboards.more_screenshots", lang),
                    callback_data=f"{CLUE_GALLERY_MORE_PREFIX}{page.provider}:{more}",
                )
            ]
        )
    rows.append(_clue_back_row(lang))
    return InlineKeyboardMarkup(rows)


def clue_empty_warning_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Confirm's warning that the author's list is empty (issue #453)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    i18n.t("keyboards.clue_start_anyway", lang),
                    callback_data=CLUE_PANEL_START_RANDOM_CALLBACK_DATA,
                )
            ],
            [
                InlineKeyboardButton(
                    i18n.t("keyboards.clue_add_screenshots", lang),
                    callback_data=CLUE_PANEL_OPEN_CALLBACK_DATA,
                )
            ],
        ]
    )


def parse_clue_gallery(data: str) -> tuple[str, Provider, int] | None:
    """A `cluegal:` button as (prefix, provider, number): the source's
    first page (number 0), a later page's offset, or a pick's index. None
    (logged) for a forged one."""
    if data.startswith(CLUE_GALLERY_SOURCE_PREFIX):
        provider = _validated_provider(data.removeprefix(CLUE_GALLERY_SOURCE_PREFIX), data=data)
        return None if provider is None else (CLUE_GALLERY_SOURCE_PREFIX, provider, 0)
    for prefix in (CLUE_GALLERY_MORE_PREFIX, CLUE_GALLERY_PICK_PREFIX):
        if data.startswith(prefix):
            parsed = _parse_provider_and_index(data.removeprefix(prefix), data=data)
            return None if parsed is None else (prefix, *parsed)
    logger.warning("ignoring unknown clue gallery button {data!r}", data=data)
    return None


def parse_clue_mode(data: str) -> ClueScreenshotMode | None:
    """The mode a `cluepanel:mode:<mode>` button names, or None (logged)
    for a forged one."""
    raw = data.removeprefix(CLUE_PANEL_MODE_PREFIX)
    try:
        return ClueScreenshotMode(raw)
    except ValueError:
        logger.warning("ignoring unknown clue screenshot mode {raw!r}", raw=raw, data=data)
        return None


def parse_clue_remove(data: str) -> int | None:
    """The entry id a `cluepanel:remove:<id>` button names, or None (logged)."""
    return _validated_index(data.removeprefix(CLUE_PANEL_REMOVE_PREFIX), data=data)
