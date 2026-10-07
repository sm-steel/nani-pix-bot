from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import PeriodType
from nani_pix_bot.models.period import PeriodState
from nani_pix_bot.services.achievements import periods

UTC_ZONE = ZoneInfo("UTC")


def test_first_start_only_arms_the_running_periods(session: Session) -> None:
    now = datetime(2026, 10, 7, 12, tzinfo=UTC)

    assert periods.finalize_due(session, now, UTC_ZONE) == []

    week = session.get(PeriodState, "week")
    assert week is not None
    assert week.next_end.replace(tzinfo=UTC) == datetime(2026, 10, 12, tzinfo=UTC)
    assert periods.next_boundary(session) == datetime(2026, 10, 12, tzinfo=UTC)


def test_downtime_finalizes_every_missed_period_oldest_first_once(session: Session) -> None:
    periods.finalize_due(session, datetime(2026, 10, 7, tzinfo=UTC), UTC_ZONE)

    later = datetime(2026, 10, 27, 9, tzinfo=UTC)  # three week boundaries later
    done = periods.finalize_due(session, later, UTC_ZONE)

    assert [p.key for p in done if p.type is PeriodType.WEEK] == [
        "2026-W41",
        "2026-W42",
        "2026-W43",
    ]
    assert periods.finalize_due(session, later, UTC_ZONE) == []


def test_coinciding_boundaries_close_week_then_month(session: Session) -> None:
    # Monday 2027-02-01 closes both week 2027-W04 and January 2027.
    periods.finalize_due(session, datetime(2027, 1, 27, tzinfo=UTC), UTC_ZONE)

    done = periods.finalize_due(session, datetime(2027, 2, 1, 0, 1, tzinfo=UTC), UTC_ZONE)

    assert [(p.type, p.key) for p in done] == [
        (PeriodType.WEEK, "2027-W04"),
        (PeriodType.MONTH, "2027-01"),
    ]


def test_the_first_boundary_is_never_earlier_than_now(session: Session) -> None:
    now = datetime(2026, 10, 7, tzinfo=UTC)
    periods.finalize_due(session, now, UTC_ZONE)

    boundary = periods.next_boundary(session)
    assert boundary is not None
    assert boundary - now < timedelta(days=8)
