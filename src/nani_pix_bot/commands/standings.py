"""/standings — the live weekly, monthly and yearly champion races in one
message (spec §6). Scoring lives in services/achievements/periods.py; this
only lays it out. Sent once, never edited, so no buttons and no callbacks."""

from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import outsider_refusal
from nani_pix_bot.commands.helpers.rich import RichTarget, md_escape, send_rich
from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import PeriodType
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.achievements import names, periods
from nani_pix_bot.services.achievements.periods import Period, Standing

TOP_SIZE = 5
PERIODS = (PeriodType.WEEK, PeriodType.MONTH, PeriodType.YEAR)


def top_rows(board: Sequence[Standing]) -> Sequence[Standing]:
    return board[:TOP_SIZE]


def you_line(board: Sequence[Standing], viewer_id: int, lang: str) -> str | None:
    """The viewer's own line: none while they are inside the top five (their
    row is enough), their rank outside it, or a note that they have no score."""
    for rank, standing in enumerate(board, start=1):
        if standing.player_id == viewer_id:
            if rank <= TOP_SIZE:
                return None
            return i18n.t(
                "standings.you", lang, rank=rank, score=standing.score, wins=standing.wins
            )
    return i18n.t("standings.you_unscored", lang)


def _header(period: Period, tz: ZoneInfo, lang: str) -> str:
    label = names.period_label(period.key, lang)
    title = i18n.t(f"standings.header.{period.type.value}", lang, label=label)
    when = period.end.astimezone(tz).strftime("%d.%m %H:%M")
    return "## " + md_escape(f"{title} · {i18n.t('standings.ends', lang, when=when)}")


def _table(session: Session, rows: Sequence[Standing], lang: str) -> list[str]:
    lines = [
        "| # | " + md_escape(i18n.t("standings.player", lang)) + " | 🌟 | 👑 |",
        "|---|---|---|---|",
    ]
    for rank, row in enumerate(rows, start=1):
        name = md_escape(players.display_name(session, row.player_id))
        lines.append(f"| {rank} | {name} | {row.score} | {row.wins} |")
    return lines


def section(session: Session, period: Period, viewer_id: int, ctx: tuple[ZoneInfo, str]) -> str:
    tz, lang = ctx
    lines = [_header(period, tz, lang)]
    board = periods.standings(session, period)
    if not board:
        lines.extend(["", md_escape(i18n.t("standings.empty", lang))])
        return "\n".join(lines)
    lines.extend(["", *_table(session, top_rows(board), lang)])
    own = you_line(board, viewer_id, lang)
    if own is not None:
        lines.extend(["", md_escape(own)])
    return "\n".join(lines)


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
    await send_rich(context.bot, target, "\n\n".join(sections))
