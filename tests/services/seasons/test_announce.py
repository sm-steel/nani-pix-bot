from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.services.seasons import lifecycle, schedule

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _kinds(session) -> list[str]:
    stmt = select(AnnouncementOutbox).order_by(AnnouncementOutbox.id)
    return [r.kind for r in session.scalars(stmt)]


def _request(start_at, end_at) -> schedule.ScheduleRequest:
    return schedule.ScheduleRequest(run_id="demo_1", start_at=start_at, end_at=end_at, admin_id=7)


def test_schedule_and_date_changes_queue_teasers_and_cancel_does_not(session) -> None:
    row = schedule.schedule(
        session, _request(NOW + timedelta(days=1), NOW + timedelta(days=5)), NOW
    )
    schedule.set_end(session, NOW + timedelta(days=6), NOW)
    schedule.cancel(session, NOW)
    assert _kinds(session) == ["season_teaser", "season_teaser"]
    rows = session.scalars(select(AnnouncementOutbox))
    assert all(r.payload == {"season_id": row.id} for r in rows)


def test_start_change_queues_a_teaser(session) -> None:
    schedule.schedule(session, _request(NOW + timedelta(days=1), NOW + timedelta(days=5)), NOW)
    schedule.set_start(session, NOW + timedelta(days=2), NOW)
    assert _kinds(session) == ["season_teaser", "season_teaser"]


def test_start_and_end_queue_their_posts_in_order(session) -> None:
    schedule.schedule(
        session,
        _request(NOW - timedelta(days=2), NOW - timedelta(days=1)),
        NOW - timedelta(days=3),
    )
    lifecycle.advance(session, NOW)
    assert _kinds(session) == ["season_teaser", "season_start", "season_end"]
