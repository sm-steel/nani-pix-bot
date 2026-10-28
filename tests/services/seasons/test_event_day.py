from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.seasons import event_day

MSK = ZoneInfo("Europe/Moscow")
UTC_TZ = ZoneInfo("UTC")


def _season(start: datetime, end: datetime) -> SeasonSchedule:
    return SeasonSchedule(run_id="demo_3", start_at=start, end_at=end, created_by=9)


def _demo3():
    run = registry.get("demo_3")
    assert run is not None
    assert run.event_day is not None
    return run


def test_event_day_window_is_the_local_day() -> None:
    season = _season(datetime(2026, 10, 26, 3, tzinfo=UTC), datetime(2026, 11, 9, tzinfo=UTC))
    assert event_day.window(_demo3(), season, MSK) == (
        datetime(2026, 10, 30, 21, tzinfo=UTC),
        datetime(2026, 10, 31, 21, tzinfo=UTC),
    )


def test_no_window_when_the_day_is_outside_the_season() -> None:
    season = _season(datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 15, tzinfo=UTC))
    assert event_day.window(_demo3(), season, MSK) is None


def test_feb_29_has_no_window_in_a_common_year() -> None:
    demo3 = _demo3()
    run = replace(demo3, event_day=replace(demo3.event_day, month=2, day=29))
    season = _season(datetime(2027, 2, 20, tzinfo=UTC), datetime(2027, 3, 5, tzinfo=UTC))
    assert event_day.window(run, season, UTC_TZ) is None


def test_window_spanning_new_year_finds_the_right_year() -> None:
    demo3 = _demo3()
    run = replace(demo3, event_day=replace(demo3.event_day, month=12, day=31))
    season = _season(datetime(2026, 12, 25, tzinfo=UTC), datetime(2027, 1, 8, tzinfo=UTC))
    assert event_day.window(run, season, UTC_TZ) == (
        datetime(2026, 12, 31, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_window_in_the_second_year_of_a_spanning_season() -> None:
    demo3 = _demo3()
    run = replace(demo3, event_day=replace(demo3.event_day, month=1, day=2))
    season = _season(datetime(2026, 12, 25, tzinfo=UTC), datetime(2027, 1, 8, tzinfo=UTC))
    assert event_day.window(run, season, UTC_TZ) == (
        datetime(2027, 1, 2, tzinfo=UTC),
        datetime(2027, 1, 3, tzinfo=UTC),
    )


def test_no_event_day_field_no_window() -> None:
    demo1 = registry.get("demo_1")
    season = _season(datetime(2026, 10, 26, tzinfo=UTC), datetime(2026, 11, 9, tzinfo=UTC))
    assert demo1 is not None
    assert event_day.window(demo1, season, UTC_TZ) is None


def test_is_event_day_reads_the_active_season(session, haunted_season, set_group_tz) -> None:
    set_group_tz("Europe/Moscow")
    assert event_day.is_event_day(session, datetime(2026, 10, 31, 20, 59, tzinfo=UTC))
    assert not event_day.is_event_day(session, datetime(2026, 10, 31, 21, 0, tzinfo=UTC))


def test_is_event_day_without_an_active_season(session) -> None:
    assert not event_day.is_event_day(session, datetime(2026, 10, 31, tzinfo=UTC))
