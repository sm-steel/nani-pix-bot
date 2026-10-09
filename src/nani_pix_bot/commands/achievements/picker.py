"""The compare picker (spec §5): a paginated list of the other players with
achievements, best first, each a button that opens the Compare table."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.commands.achievements import browser
from nani_pix_bot.commands.achievements.compare import compare_data
from nani_pix_bot.commands.helpers import paging
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.achievements import status
from nani_pix_bot.services.achievements.status import CompareFilter, View

PICK_PAGE_SIZE = 10


def _table(session: Session, rows: list[status.TopRow], offset: int, lang: str) -> str:
    lines = [
        "## " + md_escape(i18n.t("achievements.pick.header", lang)),
        "",
        "| # | "
        + md_escape(i18n.t("achievements.top.player", lang))
        + " | "
        + md_escape(i18n.t("achievements.top.points", lang))
        + " |",
        "|---|---|---|",
    ]
    for rank, row in enumerate(rows, start=offset + 1):
        name = md_escape(players.display_name(session, row.player_id))
        lines.append(f"| {rank} | {name} | {row.points} |")
    return "\n".join(lines)


def pick_view(session: Session, viewer_id: int, page: int, lang: str) -> browser.Rendered:
    back = [InlineKeyboardButton("↩", callback_data=browser.view_data(viewer_id, View.ALL, 0))]
    total = status.ranked_count(session, exclude=viewer_id)
    if total == 0:
        return i18n.t("achievements.pick.empty", lang), InlineKeyboardMarkup([back])
    page = paging.clamp_page(page, total=total, size=PICK_PAGE_SIZE)
    offset = page * PICK_PAGE_SIZE
    rows = status.top(session, limit=PICK_PAGE_SIZE, offset=offset, exclude=viewer_id)
    buttons = [
        [
            InlineKeyboardButton(
                f"{players.display_name(session, row.player_id)} · {row.points} 🏆",
                callback_data=compare_data(row.player_id, CompareFilter.ALL, 0),
            )
        ]
        for row in rows
    ]
    nav = paging.nav_row(browser.pick_data, page, paging.page_count(total, PICK_PAGE_SIZE), lang)
    keyboard = buttons + ([nav] if nav else []) + [back]
    return _table(session, rows, offset, lang), InlineKeyboardMarkup(keyboard)


def pick_tap(session: Session, viewer_id: int, fields: list, lang: str) -> browser.Rendered:
    logger.info("opened the compare picker (page {page})", page=fields[0] + 1)
    return pick_view(session, viewer_id, fields[0], lang)
