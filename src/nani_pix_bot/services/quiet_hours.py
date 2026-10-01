"""Quiet hours — a daily wall-clock window, in an admin-chosen IANA
timezone, during which the bot holds back its automatic posts and game
clocks don't run. Pure datetime math only (no DB/Telegram) — see
MECHANICS.md's "Quiet hours" section. Windows are [start, end) in local
time and may cross midnight; DST is handled by resolving each day's
window through zoneinfo rather than storing a fixed UTC offset."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time
    tz: ZoneInfo

    def __post_init__(self) -> None:
        if self.start == self.end:
            raise ValueError("Quiet hours start and end must differ")


def _as_utc(at: datetime) -> datetime:
    # DATETIME columns round-trip from the DB naive — they're UTC by convention.
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def _window_on(qh: QuietHours, day: date) -> tuple[datetime, datetime]:
    """The UTC [start, end) of the window beginning on local date `day`."""
    end_day = day if qh.end > qh.start else day + timedelta(days=1)
    start = datetime.combine(day, qh.start, tzinfo=qh.tz).astimezone(UTC)
    end = datetime.combine(end_day, qh.end, tzinfo=qh.tz).astimezone(UTC)
    return start, end


def _windows_near(qh: QuietHours, at: datetime) -> list[tuple[datetime, datetime]]:
    """Windows beginning on the local days from the day before `at`
    through two days after — always enough to cover the window
    containing `at` and the next one after it."""
    local_day = at.astimezone(qh.tz).date()
    return [_window_on(qh, local_day + timedelta(days=offset)) for offset in range(-1, 3)]


def is_quiet(qh: QuietHours | None, at: datetime) -> bool:
    if qh is None:
        return False
    at = _as_utc(at)
    return any(start <= at < end for start, end in _windows_near(qh, at))


def window_end_after(qh: QuietHours, at: datetime) -> datetime:
    """The end of the window containing `at`, or `at` itself (as UTC) if
    it's not inside one."""
    at = _as_utc(at)
    for start, end in _windows_near(qh, at):
        if start <= at < end:
            return end
    return at


def _next_window_start(qh: QuietHours, at: datetime) -> datetime:
    return min(start for start, _ in _windows_near(qh, at) if start > at)


def add_active_time(qh: QuietHours | None, at: datetime, duration: timedelta) -> datetime:
    """`at + duration`, counting only time outside quiet windows. A result
    that would land exactly on a window's start rolls to that window's
    end instead — a deadline is never left sitting at a quiet instant."""
    if qh is None:
        return at + duration
    cursor = _as_utc(at)
    remaining = duration
    while True:
        cursor = window_end_after(qh, cursor)
        next_start = _next_window_start(qh, cursor)
        if cursor + remaining < next_start:
            return cursor + remaining
        remaining -= next_start - cursor
        cursor = next_start


def utc_window(qh: QuietHours, on: datetime) -> tuple[time, time]:
    """The UTC wall-clock times of the window starting on `on`'s local
    date — shown next to the local times so an admin can sanity-check
    which zone they configured. DST can shift this by an hour."""
    start, end = _window_on(qh, _as_utc(on).astimezone(qh.tz).date())
    return start.time(), end.time()


def parse_hhmm(text: str) -> time | None:
    match = _HHMM.match(text.strip())
    if match is None:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def parse_timezone(text: str) -> ZoneInfo | None:
    if not text:
        return None
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        return None
