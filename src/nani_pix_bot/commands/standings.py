"""/standings — the live weekly, monthly and yearly champion races in one
message (spec §6). Scoring lives in services/achievements/periods.py; this
only lays it out. Sent once, never edited. Its two buttons are deep links
into the DM (how points work, recent changes — standings_dm.py), so they
need no callback here."""

from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import dm_link, outsider_refusal
from nani_pix_bot.commands.helpers.durations import clock
from nani_pix_bot.commands.helpers.rich import RichTarget, md_escape, send_rich
from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.commands.standings_dm import RECENT_PAYLOAD, RULES_PAYLOAD
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import PeriodType
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.achievements import names, periods
from nani_pix_bot.services.achievements.periods import Period, Placed, Standing

TOP_SIZE = 5
PERIODS = (PeriodType.WEEK, PeriodType.MONTH, PeriodType.YEAR)


def top_rows(board: Sequence[Standing]) -> list[Placed]:
    """The first five rows; a tie keeps its shared rank, so five rows can
    read 1, 1, 3, 4, 4."""
    return periods.ranked(board)[:TOP_SIZE]


def solve_time(standing: Standing) -> str:
    """Total ⏱ over the period's wins, to the second (it can decide a tie);
    '—' when there is none to show (a host-only score, or wins logged
    without a start time)."""
    return clock(standing.seconds) if standing.seconds > 0 else "—"


def you_line(board: Sequence[Standing], viewer_id: int, lang: str) -> str | None:
    """The viewer's own line: none while they are inside the top five rows
    (their row is enough), their shared rank and tie-breakers outside them,
    or a note that they have no score."""
    for index, (rank, standing) in enumerate(periods.ranked(board)):
        if standing.player_id == viewer_id:
            if index < TOP_SIZE:
                return None
            return i18n.t(
                "standings.you",
                lang,
                rank=rank,
                score=standing.score,
                wins=standing.wins,
                wrong=standing.wrong,
                time=solve_time(standing),
            )
    return i18n.t("standings.you_unscored", lang)


def _header(period: Period, tz: ZoneInfo, lang: str) -> str:
    label = names.period_label(period.key, lang)
    title = i18n.t(f"standings.header.{period.type.value}", lang, label=label)
    when = period.end.astimezone(tz).strftime("%d.%m %H:%M")
    return "## " + md_escape(f"{title} · {i18n.t('standings.ends', lang, when=when)}")


def table(session: Session, rows: Sequence[Placed], lang: str) -> list[str]:
    lines = [
        "| # | " + md_escape(i18n.t("standings.player", lang)) + " | 🌟 | 👑 | ❌ | ⏱ |",
        "|---|---|---|---|---|---|",
    ]
    for rank, row in rows:
        name = md_escape(players.display_name(session, row.player_id))
        cells = [str(rank), name, str(row.score), str(row.wins), str(row.wrong)]
        cells.append(md_escape(solve_time(row)))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def section(session: Session, period: Period, viewer_id: int, ctx: tuple[ZoneInfo, str]) -> str:
    tz, lang = ctx
    lines = [_header(period, tz, lang)]
    board = periods.standings(session, period)
    if not board:
        lines.extend(["", md_escape(i18n.t("standings.empty", lang))])
        return "\n".join(lines)
    lines.extend(["", *table(session, top_rows(board), lang)])
    own = you_line(board, viewer_id, lang)
    if own is not None:
        lines.extend(["", md_escape(own)])
    return "\n".join(lines)


def dm_buttons(context: ContextTypes.DEFAULT_TYPE, lang: str) -> InlineKeyboardMarkup | None:
    """How-points-work and recent-changes, both opening the DM; none until
    the bot knows its own username."""
    buttons = []
    for key, payload in (("rules", RULES_PAYLOAD), ("recent", RECENT_PAYLOAD)):
        url = dm_link(context, payload)
        if url is None:
            return None
        buttons.append(InlineKeyboardButton(i18n.t(f"standings.button.{key}", lang), url=url))
    return InlineKeyboardMarkup([[button] for button in buttons])


async def standings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return
    bot_data = context.bot_data
    if is_private_chat(update):
        refusal = await outsider_refusal(context, user.id, "standings")
        if refusal is not None:
            await message.reply_text(refusal)
            return
    elif not is_game_topic(
        update, group_chat_id=bot_data["group_chat_id"], game_topic_id=bot_data["game_topic_id"]
    ):
        return
    now = datetime.now(UTC)
    with session_scope(bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        tz = settings.get_group_timezone(session)
        sections = [
            section(session, periods.period_at(ptype, now, tz), user.id, (tz, lang))
            for ptype in PERIODS
        ]
    logger.info("viewed the champion standings")
    target = RichTarget(message.chat_id, thread_id=message.message_thread_id)
    await send_rich(context.bot, target, "\n\n".join(sections), dm_buttons(context, lang))
