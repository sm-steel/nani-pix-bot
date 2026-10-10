from datetime import UTC, datetime, timedelta

import pytest

from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.services.seasons import schedule
from nani_pix_bot.services.seasons.schedule import Refusal, ScheduleRefusedError, ScheduleRequest

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
ADMIN = 7


def _schedule(session, **kw):
    values = {
        "run_id": "demo_1",
        "start_at": NOW + timedelta(days=1),
        "end_at": NOW + timedelta(days=11),
        "admin_id": ADMIN,
    }
    values.update(kw)
    return schedule.schedule(session, ScheduleRequest(**values), NOW)


def _refused(refusal: Refusal):
    return pytest.raises(ScheduleRefusedError, match=refusal.value)


def test_scheduling_creates_a_scheduled_row(session) -> None:
    row = _schedule(session)
    assert row.status == SeasonStatus.SCHEDULED
    assert schedule.current(session) is row
    assert schedule.active(session) is None


def test_unknown_run_is_refused(session) -> None:
    with _refused(Refusal.UNKNOWN_RUN):
        _schedule(session, run_id="nope")


def test_only_one_open_season_at_a_time(session) -> None:
    _schedule(session)
    with _refused(Refusal.BUSY):
        _schedule(session)


def test_a_run_that_was_held_cannot_be_scheduled_again(session) -> None:
    row = _schedule(session)
    row.status, row.started_at, row.ended_at = SeasonStatus.ENDED, NOW, NOW
    session.flush()
    assert schedule.held_run_ids(session) == {"demo_1"}
    assert schedule.available_runs(session) == []
    with _refused(Refusal.ALREADY_HELD):
        _schedule(session)


def test_a_cancelled_run_can_be_scheduled_again_and_history_keeps_both(session) -> None:
    first = _schedule(session)
    schedule.cancel(session, NOW)
    second = _schedule(session)
    assert first.status == SeasonStatus.CANCELLED
    assert [r.id for r in schedule.history(session)] == [second.id, first.id]


def test_end_must_be_after_start_and_in_the_future(session) -> None:
    with _refused(Refusal.BAD_WINDOW):
        _schedule(session, end_at=NOW + timedelta(hours=12))  # before start
    with _refused(Refusal.BAD_WINDOW):
        _schedule(session, start_at=NOW - timedelta(days=2), end_at=NOW - timedelta(days=1))


def test_start_in_the_past_is_allowed_and_starts_at_once(session) -> None:
    row = _schedule(session, start_at=NOW - timedelta(minutes=5))
    assert row.status == SeasonStatus.SCHEDULED  # the boundary job starts it


def test_set_start_only_before_it_starts(session) -> None:
    row = _schedule(session)
    schedule.set_start(session, NOW + timedelta(days=2), NOW)
    assert row.start_at.replace(tzinfo=UTC) == NOW + timedelta(days=2)
    row.status = SeasonStatus.ACTIVE
    with _refused(Refusal.NOT_SCHEDULED):
        schedule.set_start(session, NOW + timedelta(days=3), NOW)


def test_set_end_in_the_past_while_active_ends_now(session) -> None:
    row = _schedule(session, start_at=NOW - timedelta(days=1))
    row.status = SeasonStatus.ACTIVE
    schedule.set_end(session, NOW - timedelta(days=5), NOW)
    assert row.end_at.replace(tzinfo=UTC) == NOW


def test_set_end_while_closing_is_refused(session) -> None:
    row = _schedule(session)
    row.status = SeasonStatus.CLOSING
    with _refused(Refusal.ALREADY_ENDING):
        schedule.set_end(session, NOW + timedelta(days=1), NOW)


def test_cancel_only_a_scheduled_season(session) -> None:
    with _refused(Refusal.NOTHING_OPEN):
        schedule.cancel(session, NOW)
    row = _schedule(session)
    row.status = SeasonStatus.ACTIVE
    with _refused(Refusal.NOT_SCHEDULED):
        schedule.cancel(session, NOW)


def test_scheduling_is_logged_with_structured_fields(session, log_records) -> None:
    row = _schedule(session)
    line = next(r for r in log_records if r.message.startswith("scheduled season"))
    assert line.level == "INFO"
    assert line.extra["season_id"] == row.id
    assert line.extra["run_id"] == "demo_1"
