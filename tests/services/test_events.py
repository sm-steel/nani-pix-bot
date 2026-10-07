from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.services import events


def test_emit_writes_one_row_with_its_parties_and_data(session: Session) -> None:
    event = events.emit(
        session,
        EventType.GUESS,
        events.Involved(actor_id=7, subject_id=8, game_id=3),
        stage=2,
        correct=False,
    )

    (row,) = session.scalars(select(EventLog)).all()
    assert row.event_type == EventType.GUESS
    assert (row.actor_id, row.subject_id, row.game_id) == (7, 8, 3)
    assert row.data == {"stage": 2, "correct": False}
    assert event.id == row.id
    assert event.data == {"stage": 2, "correct": False}


def test_logged_event_times_are_utc_aware(session: Session) -> None:
    event = events.emit(session, EventType.GUESS, events.Involved(actor_id=1))
    session.commit()

    row = session.scalars(select(EventLog)).one()
    loaded = events.to_event(row)

    assert event.occurred_at.tzinfo is UTC
    assert loaded.occurred_at.tzinfo is UTC
    assert abs((loaded.occurred_at - datetime.now(UTC)).total_seconds()) < 5


def test_emit_with_no_parties_is_allowed(session: Session) -> None:
    event = events.emit(session, EventType.OVERTHROWN, events.Involved())

    assert event.actor_id is None
    assert event.game_id is None
