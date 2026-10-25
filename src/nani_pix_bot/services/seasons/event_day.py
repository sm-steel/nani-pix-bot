"""The event day of a season run: one local calendar day, read in the
group's timezone (Halloween spec, "Night Parade")."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import OutboxKind
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.events import as_utc
from nani_pix_bot.services.seasons import schedule
from nani_pix_bot.services.settings import bot_settings

Window = tuple[datetime, datetime]  # UTC, [start, end)


def _local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tz).astimezone(UTC)


def window(run: SeasonRun, season: SeasonSchedule, tz: ZoneInfo) -> Window | None:
    """The event day's UTC window that overlaps the season, or None when the
    run has no event day or its month-day doesn't exist in those years."""
    if run.event_day is None:
        return None
    start_at, end_at = as_utc(season.start_at), as_utc(season.end_at)
    for year in range(start_at.astimezone(tz).year, end_at.astimezone(tz).year + 1):
        try:
            day = date(year, run.event_day.month, run.event_day.day)
        except ValueError:
            continue
        start = _local_midnight(day, tz)
        end = _local_midnight(day + timedelta(days=1), tz)
        if start < end_at and end > start_at:
            return start, end
    return None


def window_for_game(session: Session, game: Game) -> Window | None:
    if game.season_id is None:
        return None
    season = session.get(SeasonSchedule, game.season_id)
    if season is None:
        return None
    run = registry.get(season.run_id)
    if run is None:
        return None
    return window(run, season, bot_settings.get_group_timezone(session))


def is_event_day(session: Session, now: datetime) -> bool:
    season = schedule.active(session)
    if season is None:
        return False
    run = registry.get(season.run_id)
    if run is None:
        return False
    found = window(run, season, bot_settings.get_group_timezone(session))
    return found is not None and found[0] <= now < found[1]


def _window_of(session: Session, row: SeasonSchedule) -> Window | None:
    run = registry.get(row.run_id)
    if run is None:
        return None
    return window(run, row, bot_settings.get_group_timezone(session))


def _queued(session: Session, row: SeasonSchedule, kind: OutboxKind) -> bool:
    """Whether a post of this kind was ever queued for the season, posted or not."""
    stmt = select(AnnouncementOutbox.payload).where(AnnouncementOutbox.kind == kind)
    return any(payload.get("season_id") == row.id for payload in session.scalars(stmt))


def due_posts(
    session: Session, row: SeasonSchedule, now: datetime, *, finalizing: bool = False
) -> list[OutboxKind]:
    """The parade posts owed right now, in posting order. A start is owed
    only while the day is on, so downtime over the whole day posts nothing
    stale; a sunrise is owed only after a start went out, and a season that
    is ending (`finalizing`) mid-day closes the parade first."""
    found = _window_of(session, row)
    if found is None:
        return []
    start, end = found
    start_queued = _queued(session, row, OutboxKind.EVENT_DAY_START)
    due: list[OutboxKind] = []
    if start <= now < end and not start_queued:
        due.append(OutboxKind.EVENT_DAY_START)
    if (
        (now >= end or finalizing)
        and start_queued
        and not _queued(session, row, OutboxKind.EVENT_DAY_END)
    ):
        due.append(OutboxKind.EVENT_DAY_END)
    return due


def enqueue_due(
    session: Session, row: SeasonSchedule, now: datetime, *, finalizing: bool = False
) -> list[OutboxKind]:
    run = registry.get(row.run_id)
    event = run.event_day if run is not None else None
    if event is None:
        return []
    due = due_posts(session, row, now, finalizing=finalizing)
    for kind in due:
        outbox.enqueue_season(session, kind, row.id)
        logger.info(
            "queued the {kind} post for {event_day_name}",
            kind=kind.value,
            event_day_name=event.names["EN"],
            season_id=row.id,
        )
    return due


def next_boundary(session: Session, row: SeasonSchedule, now: datetime) -> datetime | None:
    """The next edge of the event-day window after `now`, or None."""
    found = _window_of(session, row)
    if found is None:
        return None
    return next((edge for edge in found if edge > now), None)
