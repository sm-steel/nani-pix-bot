"""The "what my card shows" editor (#413), opened with ✏️ on your own card in
DM, in the card's own message: tabs for season stamps and achievements, one
button per thing you hold (✅ when shown), ◀ n/N ▶, and ↩ Done back to the
card. A tap shows or hides it (services/cosmetics/showcase.py); the order you
tick is the order on the card. Callback data: data.py."""

from dataclasses import dataclass
from typing import cast

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import paging
from nani_pix_bot.commands.helpers.keyboards import tab_button
from nani_pix_bot.commands.profile.data import KINDS, card_data, edit_data, toggle_data
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import ShowcaseKind
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements import catalogue, names
from nani_pix_bot.services.cosmetics import showcase
from nani_pix_bot.services.cosmetics.showcase import Toggle

PAGE_SIZE = 8
SHOWN = "✅ "
HIDDEN = "▫️ "
_TABS = {ShowcaseKind.STAMP: "stamps", ShowcaseKind.ACHIEVEMENT: "achievements"}


def _stamp_options(session: Session, owner: int, lang: str) -> list[tuple[int, str, str]]:
    """(season row id, run id, label) per season played."""
    options = []
    for season_id, run_id in showcase.stamp_seasons(session, owner):
        run = registry.get(run_id)
        options.append((season_id, run_id, run.name(lang) if run else run_id))
    return options


def _achievement_options(session: Session, owner: int, lang: str) -> list[tuple[int, str, str]]:
    """(grant id, grant id as the pick ref, label) per achievement held."""
    options = []
    for held in showcase.badge_choices(session, owner):
        try:
            defn = catalogue.get(held.key)
        except KeyError:
            continue
        options.append((held.grant_id, str(held.grant_id), names.name(defn, held.tier, lang)))
    return options


def _options(session: Session, owner: int, kind: ShowcaseKind, lang: str):
    if kind is ShowcaseKind.STAMP:
        return _stamp_options(session, owner, lang)
    return _achievement_options(session, owner, lang)


@dataclass(frozen=True)
class EditorPage:
    owner: int
    kind: ShowcaseKind
    page: int


def editor_view(
    session: Session, at: EditorPage, lang: str
) -> tuple[str, InlineKeyboardMarkup, int]:
    """The editor's caption, its keyboard and the page actually shown."""
    owner, kind = at.owner, at.kind
    tabs = [
        tab_button(
            i18n.t(f"profile.edit.tab_{_TABS[k]}", lang),
            current=k is kind,
            callback_data=edit_data(owner, k, 0),
        )
        for k in ShowcaseKind
    ]
    options = _options(session, owner, kind, lang)
    held = {ref for _id, ref, _label in options}
    picked = [ref for ref in showcase.picks(session, owner, kind) if ref in held]
    page = paging.clamp_page(at.page, total=len(options), size=PAGE_SIZE)
    rows = [tabs]
    for ref_id, ref, label in options[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
        mark = SHOWN if ref in picked else HIDDEN
        data = toggle_data(owner, kind, ref_id, page)
        rows.append([InlineKeyboardButton(mark + label, callback_data=data)])
    pages = paging.page_count(len(options), PAGE_SIZE)
    nav = paging.nav_row(lambda p: edit_data(owner, kind, p), page, pages, lang)
    rows += [nav] if nav else []
    done = i18n.t("profile.edit.done", lang)
    rows.append([InlineKeyboardButton(done, callback_data=card_data(owner, owner, "m"))])
    if not options:
        caption = i18n.t(f"profile.edit.none_{_TABS[kind]}", lang)
    else:
        limit = showcase.LIMITS[kind]
        caption = i18n.t(f"profile.edit.{_TABS[kind]}", lang, count=len(picked), limit=limit)
    return caption, InlineKeyboardMarkup(rows), page


async def _redraw(query: CallbackQuery, session_factory, at: EditorPage, lang: str) -> int | None:
    """Edit the message into the editor; the page shown, or None if that failed."""
    with session_scope(session_factory) as session:
        caption, keyboard, page = editor_view(session, at, lang)
    try:
        await query.edit_message_caption(caption=caption, reply_markup=keyboard)
    except TelegramError:
        logger.opt(exception=True).warning("could not show the /profile editor")
        return None
    return page


def _in_dm(query: CallbackQuery) -> bool:
    message = query.message
    if message is not None and message.chat.type == ChatType.PRIVATE:
        return True
    logger.warning("refused a /profile editor button outside a private chat")
    return False


async def editor_tap(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, fields: list[int | str], lang: str
) -> None:
    owner, code, page = cast(tuple[int, str, int], tuple(fields))
    if not _in_dm(query):
        await query.answer()
        return
    kind = KINDS[code]
    at = EditorPage(owner, kind, page)
    shown = await _redraw(query, context.bot_data["session_factory"], at, lang)
    await query.answer()
    if shown is not None:
        logger.info(
            "opened the /profile editor: {kind} (page {page})", kind=kind.value, page=shown + 1
        )


def _ref(session: Session, kind: ShowcaseKind, ref_id: int) -> str | None:
    """The pick ref a button's id stands for: a season's run id, or the grant id."""
    if kind is ShowcaseKind.ACHIEVEMENT:
        return str(ref_id)
    season = session.get(SeasonSchedule, ref_id)
    return season.run_id if season is not None else None


async def toggle_tap(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, fields: list[int | str], lang: str
) -> None:
    owner, code, ref_id, page = cast(tuple[int, str, int, int], tuple(fields))
    if not _in_dm(query):
        await query.answer()
        return
    kind = KINDS[code]
    with session_scope(context.bot_data["session_factory"]) as session:
        ref = _ref(session, kind, ref_id)
        result = Toggle.UNKNOWN if ref is None else showcase.toggle(session, owner, kind, ref)
    if result is Toggle.FULL:
        limit = showcase.LIMITS[kind]
        await query.answer(i18n.t("profile.edit.full", lang, limit=limit), show_alert=True)
        return
    if result is Toggle.UNKNOWN:
        if ref is None:
            logger.warning(
                "a /profile editor button pointed at unknown season {ref_id}", ref_id=ref_id
            )
        await query.answer(i18n.t("profile.edit.gone", lang), show_alert=True)
        return
    await _redraw(query, context.bot_data["session_factory"], EditorPage(owner, kind, page), lang)
    await query.answer()
