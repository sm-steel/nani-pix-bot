"""/season — DM only, group admins (seasons spec §1). Dates in the admin's
own /timezone; every reply echoes them back in that zone."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.admin_dm import AdminDm, admin_dm
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.seasons import schedule_season_job
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.events import as_utc
from nani_pix_bot.services.seasons import schedule
from nani_pix_bot.services.seasons.schedule import ScheduleRefusedError, ScheduleRequest
from nani_pix_bot.services.seasons.when import parse_local


def _fmt(at: datetime, tz: ZoneInfo) -> str:
    return as_utc(at).astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _state(row: SeasonSchedule, lang: str) -> str:
    return i18n.t(f"season.state.{row.status}", lang)


def _current_line(session: Session, row: SeasonSchedule, tz: ZoneInfo, lang: str) -> str:
    run = registry.get(row.run_id)
    return i18n.t(
        "season.status.current",
        lang,
        name=run.name(lang) if run else row.run_id,
        run_id=row.run_id,
        status=_state(row, lang),
        start=_fmt(row.start_at, tz),
        end=_fmt(row.end_at, tz),
        tz=tz.key,
        admin=players.display_name(session, row.created_by),
    )


def _history_lines(
    session: Session, current: SeasonSchedule | None, tz: ZoneInfo, lang: str
) -> list[str]:
    past = [r for r in schedule.history(session, 5) if r is not current]
    if not past:
        return []
    header = i18n.t("season.status.history", lang)
    return [header] + [
        i18n.t(
            "season.status.history_line",
            lang,
            run_id=r.run_id,
            status=_state(r, lang),
            start=_fmt(r.start_at, tz),
            admin=players.display_name(session, r.created_by),
        )
        for r in past
    ]


def _status_text(session: Session, tz: ZoneInfo, lang: str) -> str:
    row = schedule.current(session)
    lines = [
        i18n.t("season.status.none", lang) if row is None else _current_line(session, row, tz, lang)
    ]
    runs = schedule.available_runs(session)
    lines.append(
        i18n.t("season.status.available", lang) if runs else i18n.t("season.status.no_runs", lang)
    )
    lines += [f"• {run.run_id} — {run.name(lang)}" for run in runs]
    lines += _history_lines(session, row, tz, lang)
    return "\n".join(lines)


def _when(args: list[str], tz: ZoneInfo) -> datetime | None:
    if args == ["now"]:
        return datetime.now(UTC)
    return parse_local(args[0], args[1], tz) if len(args) == 2 else None


def _schedule(session: Session, dm: AdminDm, args: list[str], tz: ZoneInfo) -> str | None:
    start, end = parse_local(args[1], args[2], tz), parse_local(args[3], args[4], tz)
    if start is None or end is None:
        return None
    request = ScheduleRequest(run_id=args[0], start_at=start, end_at=end, admin_id=dm.user_id)
    row = schedule.schedule(session, request, datetime.now(UTC))
    return i18n.t(
        "season.done.scheduled",
        dm.lang,
        run_id=row.run_id,
        start=_fmt(row.start_at, tz),
        end=_fmt(row.end_at, tz),
        tz=tz.key,
    )


def _move(session: Session, dm: AdminDm, action: str, args: list[str], tz: ZoneInfo) -> str | None:
    at = None if (action == "start" and args == ["now"]) else _when(args, tz)
    if at is None:
        return None
    setter = schedule.set_start if action == "start" else schedule.set_end
    row = setter(session, at, datetime.now(UTC))
    return i18n.t(
        "season.done.dates",
        dm.lang,
        start=_fmt(row.start_at, tz),
        end=_fmt(row.end_at, tz),
        tz=tz.key,
    )


def _apply(session: Session, dm: AdminDm, args: list[str], tz: ZoneInfo) -> str | None:
    """Runs one subcommand and returns the reply text, or None when the
    arguments don't fit any subcommand. Raises ScheduleRefusedError for a
    rule the change breaks."""
    action, rest = args[0], args[1:]
    if action == "schedule" and len(rest) == 5:
        return _schedule(session, dm, rest, tz)
    if action in ("start", "end"):
        return _move(session, dm, action, rest, tz)
    if action == "cancel" and not rest:
        row = schedule.cancel(session, datetime.now(UTC))
        return i18n.t("season.done.cancelled", dm.lang, run_id=row.run_id)
    return None


def _run_subcommand(
    session: Session, dm: AdminDm, args: list[str], tz: ZoneInfo
) -> tuple[str, bool]:
    """(reply text, whether the schedule changed)."""
    try:
        text = _apply(session, dm, args, tz)
    except ScheduleRefusedError as refused:
        logger.warning(
            "/season {args} refused: {refusal}",
            args=" ".join(args),
            refusal=refused.refusal.value,
        )
        return i18n.t(f"season.refused.{refused.refusal.value}", dm.lang), False
    if text is None:
        logger.warning("sent invalid /season args {args!r}", args=args)
        return i18n.t("season.usage", dm.lang), False
    return text, True


async def season_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    dm = await admin_dm(update, context, "season")
    if dm is None:
        return
    args = context.args or []
    changed = False
    with session_scope(context.bot_data["session_factory"]) as session:
        tz = players.get_timezone(session, dm.user_id)
        if tz is None:
            logger.warning("tried /season without a timezone set")
            text = i18n.t("season.need_timezone", dm.lang)
        elif not args:
            logger.info("viewed /season")
            text = _status_text(session, tz, dm.lang)
        else:
            text, changed = _run_subcommand(session, dm, args, tz)
    if changed:
        schedule_season_job(context.job_queue, 0)
    await dm.message.reply_text(text)
