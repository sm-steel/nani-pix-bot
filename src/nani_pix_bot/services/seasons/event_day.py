"""The event day of a season run: one local calendar day, read in the
group's timezone (Halloween spec, "Night Parade")."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
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
