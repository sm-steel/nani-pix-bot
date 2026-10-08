"""The ◀ n/N ▶ row every paginated rich message shares (achievements,
standings feed, leaderboard, history). `make` turns a page number into that
view's own callback data; the n/N counter in the middle is NOOP, answered
by noop_callback so a tap on it doesn't leave a spinner."""

from collections.abc import Callable

from telegram import InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from nani_pix_bot.services import i18n

NOOP = "page:x"


def page_count(total: int, size: int) -> int:
    return max(1, -(-total // size))


def clamp_page(page: int, *, total: int, size: int) -> int:
    return max(0, min(page, page_count(total, size) - 1))


def nav_row(
    make: Callable[[int], str], page: int, pages: int, lang: str
) -> list[InlineKeyboardButton]:
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(
            InlineKeyboardButton(i18n.t("achievements.prev", lang), callback_data=make(page - 1))
        )
    row.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data=NOOP))
    if page < pages - 1:
        row.append(
            InlineKeyboardButton(
                i18n.t("achievements.next_page", lang), callback_data=make(page + 1)
            )
        )
    return row


async def noop_callback(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query is not None:
        await update.callback_query.answer()
