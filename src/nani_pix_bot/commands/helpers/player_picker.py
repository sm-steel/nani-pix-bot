"""The player picker every command that targets another player shows when no
@username was given: one button per candidate, PAGE_SIZE to a page, ◀ / ▶ only
when there is a page that way, and ✖ cancel. Each command supplies its own
candidates (see `services/players.py::by_activity` for the usual order), its
callback data, and what a tap does; nothing here knows what the pick is for."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.commands.helpers.paging import clamp_page
from nani_pix_bot.services import i18n

PAGE_SIZE = 8


@dataclass(frozen=True)
class PickEntry:
    player_id: int
    label: str  # the button text: the plain display name


@dataclass(frozen=True)
class PickerData:
    """The callback data a command gives its picker's buttons."""

    pick: Callable[[PickEntry], str]
    page: Callable[[int], str]
    cancel: str


def clamp(entries: Sequence[PickEntry], page: int) -> int:
    """`page` within the pages `entries` fill; an out-of-range page is the last one."""
    return clamp_page(page, total=len(entries), size=PAGE_SIZE)


def _nav_row(count: int, page: int, data: PickerData, lang: str) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(
            InlineKeyboardButton(
                i18n.t("player_picker.prev", lang), callback_data=data.page(page - 1)
            )
        )
    row.append(
        InlineKeyboardButton(i18n.t("player_picker.cancel", lang), callback_data=data.cancel)
    )
    if (page + 1) * PAGE_SIZE < count:
        row.append(
            InlineKeyboardButton(
                i18n.t("player_picker.next", lang), callback_data=data.page(page + 1)
            )
        )
    return row


def picker_keyboard(
    entries: Sequence[PickEntry], page: int, data: PickerData, lang: str
) -> InlineKeyboardMarkup:
    """One row per candidate on the (clamped) page, then the ◀ ✖ ▶ row."""
    page = clamp(entries, page)
    shown = entries[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows = [[InlineKeyboardButton(entry.label, callback_data=data.pick(entry))] for entry in shown]
    rows.append(_nav_row(len(entries), page, data, lang))
    return InlineKeyboardMarkup(rows)
