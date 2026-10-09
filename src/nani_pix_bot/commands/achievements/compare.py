"""Compare two players' achievements (spec §5): a table, three tabs, pages."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.commands.achievements import browser
from nani_pix_bot.commands.achievements.common import PREFIX
from nani_pix_bot.commands.helpers import paging
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.achievements import names, status
from nani_pix_bot.services.achievements.definitions import Kind
from nani_pix_bot.services.achievements.status import CompareFilter, State, Status

COMPARE_PAGE_SIZE = 10


def compare_data(other_id: int, filt: CompareFilter, page: int) -> str:
    return f"{PREFIX}c:{other_id}:{filt.value}:{page}"


def _cell(item: Status) -> str:
    if item.state is not State.EARNED:
        return "⬜"
    if item.defn.kind is Kind.PERIOD:
        return f"✅ \N{MULTIPLICATION SIGN}{item.tier}"
    laddered = len(item.defn.tiers) > 1 or item.defn.endless_step
    return f"✅ {names.roman(item.tier)}" if laddered else "✅"


def _label(mine: Status, theirs: Status, lang: str) -> str:
    if mine.defn.hidden and State.EARNED not in (mine.state, theirs.state):
        return "❔"
    return md_escape(names.name(mine.defn, 0, lang))


def _tabs(other_id: int, current: CompareFilter, lang: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            ("● " if f is current else "") + i18n.t(f"achievements.compare.{f.name.lower()}", lang),
            callback_data=compare_data(other_id, f, 0),
        )
        for f in CompareFilter
    ]


@dataclass(frozen=True)
class CompareRequest:
    viewer_id: int
    other_id: int
    filt: CompareFilter = CompareFilter.ALL
    page: int = 0


def _table(
    session: Session, request: CompareRequest, shown: list[tuple[Status, Status]], lang: str
) -> str:
    other = md_escape(players.display_name(session, request.other_id))
    you = md_escape(i18n.t("achievements.compare.you", lang))
    column = md_escape(i18n.t("achievements.compare.column", lang))
    lines = [
        "## ⚖️ " + md_escape(i18n.t("achievements.compare.header", lang)) + f" {other}",
        "",
        f"| {column} | {you} | {other} |",
        "|---|---|---|",
    ]
    lines += [f"| {_label(m, t, lang)} | {_cell(m)} | {_cell(t)} |" for m, t in shown]
    return "\n".join(lines)


def compare_view(session: Session, request: CompareRequest, lang: str) -> browser.Rendered:
    now = datetime.now(UTC)
    mine = status.build(session, request.viewer_id, now)
    pairs = status.compare(mine, status.build(session, request.other_id, now), request.filt)
    page = paging.clamp_page(request.page, total=len(pairs), size=COMPARE_PAGE_SIZE)
    pages = paging.page_count(len(pairs), COMPARE_PAGE_SIZE)
    shown = pairs[page * COMPARE_PAGE_SIZE : (page + 1) * COMPARE_PAGE_SIZE]
    rows = [_tabs(request.other_id, request.filt, lang)]
    nav = paging.nav_row(
        lambda p: compare_data(request.other_id, request.filt, p), page, pages, lang
    )
    if nav:
        rows.append(nav)
    back = browser.view_data(request.other_id, browser.View.ALL, 0)
    rows.append([InlineKeyboardButton("↩", callback_data=back)])
    return _table(session, request, shown, lang), InlineKeyboardMarkup(rows)


def compare_tap(
    session: Session, viewer_id: int, fields: list[int | str], lang: str
) -> browser.Rendered:
    other_id, raw_filter, page = cast(tuple[int, str, int], tuple(fields))
    known = {f.value for f in CompareFilter}
    filt = CompareFilter(raw_filter) if raw_filter in known else CompareFilter.ALL
    logger.info(
        "compared achievements with {target} ({filter}, page {page})",
        target=players.describe_player_id(session, other_id),
        target_id=other_id,
        filter=filt.value,
        page=page + 1,
    )
    return compare_view(session, CompareRequest(viewer_id, other_id, filt, page), lang)
