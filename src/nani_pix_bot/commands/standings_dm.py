"""The two DM views behind /standings' buttons: how 🌟 points work (#284)
and this week's recent gains (#285). The buttons are deep links
(`/start rules`, `/start recent`, routed by onboarding.py), so they work
even for someone who never started the bot. The feed is one rich message
paged in place with std:r:<page>; everything after the prefix is
client-controlled and parsed defensively."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID, outsider_refusal
from nani_pix_bot.commands.helpers.paging import clamp_page, nav_row, page_count
from nani_pix_bot.commands.helpers.rich import RichTarget, edit_rich, md_escape, send_rich
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import PeriodType
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.achievements import names, periods
from nani_pix_bot.services.achievements.periods import Gain, GainRole, Period

PREFIX = "std:"
RULES_PAYLOAD = "rules"
RECENT_PAYLOAD = "recent"
FEED_PAGE_SIZE = 10
Rendered = tuple[str, InlineKeyboardMarkup]


def _t(key: str, lang: str, **kwargs: object) -> str:
    return md_escape(i18n.t(key, lang, **kwargs))


def rules_markdown(lang: str, tz_name: str) -> str:
    """Every number comes from periods' constants, so the explanation can't
    drift from the scoring."""
    stages = len(periods.WIN_POINTS)
    turns = ", ".join(
        i18n.t(
            "standings.rules.hard_turn", lang, turn=turn, total=len(periods.HARD_POINTS), points=p
        )
        for turn, p in enumerate(periods.HARD_POINTS, start=1)
    )
    table = [f"| {_t('standings.rules.stage', lang)} | 🌟 |", "|---|---|"]
    table += [f"| {s}/{stages} | {p} |" for s, p in enumerate(periods.WIN_POINTS, start=1)]
    paragraphs = [
        "## " + _t("standings.rules.title", lang),
        _t("standings.rules.intro", lang),
        "\n".join(table),
        _t("standings.rules.hard", lang, turns=turns),
        _t("standings.rules.host", lang, points=periods.HOST_POINTS),
        _t("standings.rules.unsolved", lang),
        _t("standings.rules.setwinner", lang),
        _t("standings.rules.ties", lang),
        _t("standings.rules.periods", lang, tz=tz_name),
        _t("standings.rules.champions", lang, top=periods.TOP_SIZE),
        _t("standings.rules.other", lang),
    ]
    return "\n\n".join(paragraphs)


def feed_data(page: int) -> str:
    return f"{PREFIX}r:{page}"


def parse_page(data: str) -> int | None:
    action, *fields = data.removeprefix(PREFIX).split(":")
    if action != "r" or len(fields) != 1:
        return None
    raw = fields[0]
    return int(raw) if raw.isdecimal() and int(raw) <= MAX_ID else None


def _why(gain: Gain, lang: str) -> str:
    if gain.role is GainRole.HOST:
        return i18n.t("standings.recent.host", lang, game=gain.game_id)
    if gain.hard_mode:
        total = len(periods.HARD_POINTS)
        return i18n.t(
            "standings.recent.win_hard", lang, game=gain.game_id, stage=gain.stage, total=total
        )
    total = len(periods.WIN_POINTS)
    return i18n.t("standings.recent.win", lang, game=gain.game_id, stage=gain.stage, total=total)


def _rank(rank: int | None) -> str:
    return "—" if rank is None else f"#{rank}"


def _row(session: Session, gain: Gain, tz: ZoneInfo, lang: str) -> str:
    when = gain.at.astimezone(tz).strftime("%d.%m %H:%M")
    name = players.display_name(session, gain.player_id)
    move = f"{_rank(gain.rank_before)} → {_rank(gain.rank_after)}"
    cells = [when, name, f"+{gain.points}", _why(gain, lang), move]
    return "| " + " | ".join(md_escape(c) for c in cells) + " |"


def feed_view(session: Session, page: int, period: Period, tz: ZoneInfo, lang: str) -> Rendered:
    label = names.period_label(period.key, lang)
    lines = ["## " + _t("standings.recent.title", lang, label=label), ""]
    gains = periods.recent_gains(session, period)
    if not gains:
        lines.append(_t("standings.empty", lang))
        return "\n".join(lines), InlineKeyboardMarkup([])
    page = clamp_page(page, total=len(gains), size=FEED_PAGE_SIZE)
    header = [
        _t("standings.recent.when", lang),
        _t("standings.player", lang),
        "🌟",
        _t("standings.recent.why", lang),
        _t("standings.recent.rank", lang),
    ]
    lines += ["| " + " | ".join(header) + " |", "|---|---|---|---|---|"]
    shown = gains[page * FEED_PAGE_SIZE : (page + 1) * FEED_PAGE_SIZE]
    lines += [_row(session, gain, tz, lang) for gain in shown]
    nav = nav_row(feed_data, page, page_count(len(gains), FEED_PAGE_SIZE), lang)
    return "\n".join(lines), InlineKeyboardMarkup([nav] if nav else [])


def _week_feed(session: Session, page: int, lang: str) -> Rendered:
    tz = settings.get_group_timezone(session)
    week = periods.period_at(PeriodType.WEEK, datetime.now(UTC), tz)
    return feed_view(session, page, week, tz, lang)


async def open_rules(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        tz = settings.get_group_timezone(session)
    logger.info("opened how standings points work")
    await send_rich(context.bot, RichTarget(message.chat_id), rules_markdown(lang, tz.key))


async def open_recent(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown, markup = _week_feed(session, 0, lang)
    logger.info("opened the recent standings changes")
    await send_rich(context.bot, RichTarget(message.chat_id), markdown, markup)


async def standings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    page = parse_page(query.data or "")
    if page is None or query.message is None or query.from_user is None:
        logger.warning("ignored a malformed or stale standings tap {data!r}", data=query.data)
        await query.answer()
        return
    refusal = await outsider_refusal(context, query.from_user.id, "standings tap")
    await query.answer(refusal, show_alert=refusal is not None)
    if refusal is not None:
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        markdown, markup = _week_feed(session, page, lang)
    logger.info("paged the recent standings changes to page {page}", page=page + 1)
    target = RichTarget(query.message.chat.id, message_id=query.message.message_id)
    await edit_rich(context.bot, target, markdown, markup)
