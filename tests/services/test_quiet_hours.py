from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from nani_pix_bot.services import quiet_hours
from nani_pix_bot.services.quiet_hours import QuietHours

MSK = ZoneInfo("Europe/Moscow")  # UTC+3, no DST
LONDON = ZoneInfo("Europe/London")
NIGHT = QuietHours(start=time(23, 0), end=time(8, 0), tz=MSK)  # 20:00-05:00 UTC


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_start_equal_to_end_is_rejected() -> None:
    with pytest.raises(ValueError, match="differ"):
        QuietHours(start=time(8, 0), end=time(8, 0), tz=MSK)


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (utc(2026, 10, 1, 1, 0), True),  # 04:00 MSK, mid-window
        (utc(2026, 9, 30, 20, 0), True),  # 23:00 MSK, start is inclusive
        (utc(2026, 10, 1, 5, 0), False),  # 08:00 MSK, end is exclusive
        (utc(2026, 9, 30, 19, 59), False),  # 22:59 MSK
        (utc(2026, 10, 1, 12, 0), False),  # midday
    ],
)
def test_is_quiet_across_midnight(at: datetime, expected: bool) -> None:
    assert quiet_hours.is_quiet(NIGHT, at) is expected


def test_is_quiet_with_none_is_never_quiet() -> None:
    assert quiet_hours.is_quiet(None, utc(2026, 10, 1, 1, 0)) is False


def test_is_quiet_window_not_crossing_midnight() -> None:
    qh = QuietHours(start=time(1, 0), end=time(6, 0), tz=ZoneInfo("UTC"))
    assert quiet_hours.is_quiet(qh, utc(2026, 10, 1, 3, 0)) is True
    assert quiet_hours.is_quiet(qh, utc(2026, 10, 1, 7, 0)) is False


def test_naive_datetime_is_treated_as_utc() -> None:
    naive = datetime(2026, 10, 1, 1, 0)  # noqa: DTZ001 — deliberately naive, as DB round-trips are
    assert quiet_hours.is_quiet(NIGHT, naive) is True
    assert quiet_hours.window_end_after(NIGHT, naive) == utc(2026, 10, 1, 5, 0)
    assert quiet_hours.add_active_time(NIGHT, naive, timedelta(hours=1)) == utc(2026, 10, 1, 6, 0)


def test_window_end_after_inside_and_outside() -> None:
    assert quiet_hours.window_end_after(NIGHT, utc(2026, 10, 1, 1, 0)) == utc(2026, 10, 1, 5, 0)
    outside = utc(2026, 10, 1, 12, 0)
    assert quiet_hours.window_end_after(NIGHT, outside) == outside


def test_add_active_time_none_is_plain_addition() -> None:
    at = utc(2026, 10, 1, 1, 0)
    assert quiet_hours.add_active_time(None, at, timedelta(hours=6)) == at + timedelta(hours=6)


def test_add_active_time_starting_inside_window() -> None:
    # 03:00 MSK guess: clock starts at 08:00 MSK, +6h = 14:00 MSK = 11:00 UTC
    result = quiet_hours.add_active_time(NIGHT, utc(2026, 10, 1, 0, 0), timedelta(hours=6))
    assert result == utc(2026, 10, 1, 11, 0)


def test_add_active_time_straddling_a_window() -> None:
    # 21:00 MSK + 3h: 2h until 23:00, pause, then 1h after 08:00 → 09:00 MSK = 06:00 UTC
    result = quiet_hours.add_active_time(NIGHT, utc(2026, 9, 30, 18, 0), timedelta(hours=3))
    assert result == utc(2026, 10, 1, 6, 0)


def test_duration_ending_exactly_at_window_start_rolls_to_window_end() -> None:
    result = quiet_hours.add_active_time(NIGHT, utc(2026, 9, 30, 18, 0), timedelta(hours=2))
    assert result == utc(2026, 10, 1, 5, 0)


def test_add_active_time_spanning_several_windows() -> None:
    # 09:00 MSK Oct 1, 48 active hours, 15 active hours per day:
    # 14h (to 23:00) + 15h (Oct 2) + 15h (Oct 3) + 4h (Oct 4 08:00→12:00 MSK)
    result = quiet_hours.add_active_time(NIGHT, utc(2026, 10, 1, 6, 0), timedelta(days=2))
    assert result == utc(2026, 10, 4, 9, 0)


def test_dst_spring_forward_window_is_shortened() -> None:
    # 2026-03-29 London: 01:00 GMT jumps to 02:00 BST.
    # 00:30 GMT = 00:30 UTC; 02:30 BST = 01:30 UTC.
    qh = QuietHours(start=time(0, 30), end=time(2, 30), tz=LONDON)
    assert quiet_hours.is_quiet(qh, utc(2026, 3, 29, 1, 0)) is True
    assert quiet_hours.window_end_after(qh, utc(2026, 3, 29, 1, 0)) == utc(2026, 3, 29, 1, 30)
    assert quiet_hours.is_quiet(qh, utc(2026, 3, 29, 1, 30)) is False


def test_dst_fall_back_window_is_lengthened() -> None:
    # 2026-10-25 London: 02:00 BST falls back to 01:00 GMT.
    # 00:30 BST = 23:30 UTC on Oct 24; 02:30 GMT = 02:30 UTC.
    qh = QuietHours(start=time(0, 30), end=time(2, 30), tz=LONDON)
    assert quiet_hours.is_quiet(qh, utc(2026, 10, 24, 23, 45)) is True
    assert quiet_hours.window_end_after(qh, utc(2026, 10, 25, 2, 0)) == utc(2026, 10, 25, 2, 30)


def test_window_starting_in_dst_gap_still_works() -> None:
    # 01:30 London does not exist on 2026-03-29; it resolves with the pre-transition offset.
    qh = QuietHours(start=time(1, 30), end=time(3, 0), tz=LONDON)
    at = utc(2026, 3, 29, 1, 45)
    assert quiet_hours.is_quiet(qh, at) is True
    assert quiet_hours.add_active_time(qh, at, timedelta(minutes=10)) > at


def test_utc_window() -> None:
    assert quiet_hours.utc_window(NIGHT, utc(2026, 10, 1, 12, 0)) == (time(20, 0), time(5, 0))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("23:00", time(23, 0)), ("8:05", time(8, 5)), ("00:00", time(0, 0))],
)
def test_parse_hhmm_valid(text: str, expected: time) -> None:
    assert quiet_hours.parse_hhmm(text) == expected


@pytest.mark.parametrize("text", ["24:00", "23:60", "2300", "", "ab:cd", "-1:00"])
def test_parse_hhmm_invalid(text: str) -> None:
    assert quiet_hours.parse_hhmm(text) is None


def test_parse_timezone() -> None:
    assert quiet_hours.parse_timezone("Europe/Moscow") == MSK
    assert quiet_hours.parse_timezone("Mars/Olympus") is None
    assert quiet_hours.parse_timezone("../etc/passwd") is None
    assert quiet_hours.parse_timezone("") is None


@pytest.mark.parametrize("text", ["Europe", "Etc", "Asia", "A" * 300])
def test_parse_timezone_rejects_directory_like_and_overlong_names(text: str) -> None:
    # Bare region names resolve to a directory inside the tzdata package and
    # an overlong name hits the OS path limit — both raise OSError from
    # zoneinfo, which must not escape as a crash.
    assert quiet_hours.parse_timezone(text) is None
