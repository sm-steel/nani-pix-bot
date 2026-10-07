from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import event
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.services import events
from nani_pix_bot.services.achievements.history import DbHistory


def test_db_history_splits_actor_subject_and_group_by_type(session: Session) -> None:
    events.emit(session, EventType.GAME_WON, events.Involved(actor_id=1, subject_id=2, game_id=9))
    events.emit(session, EventType.GAME_WON, events.Involved(actor_id=3, subject_id=1, game_id=10))
    events.emit(session, EventType.GUESS, events.Involved(actor_id=1, game_id=10))

    history = DbHistory(session, 1, ZoneInfo("UTC"))

    assert [e.game_id for e in history.mine(EventType.GAME_WON)] == [9]
    assert [e.game_id for e in history.about_me(EventType.GAME_WON)] == [10]
    assert [e.game_id for e in history.group(EventType.GAME_WON)] == [9, 10]
    assert [e.event_type for e in history.mine(EventType.GAME_WON, EventType.GUESS)] == [
        EventType.GAME_WON,
        EventType.GUESS,
    ]


def _at(session: Session, event_type: EventType, actor: int, at: datetime, **data: object) -> None:
    naive = at.replace(tzinfo=None)  # how DATETIME columns store it
    session.add(EventLog(event_type=event_type, actor_id=actor, occurred_at=naive, data=data))
    session.flush()


def test_days_are_local_dates_without_bot_started_hosting(session: Session) -> None:
    msk = ZoneInfo("Europe/Moscow")
    late = datetime(2026, 10, 5, 22, 30, tzinfo=UTC)  # Oct 6, 01:30 in Moscow
    _at(session, EventType.GUESS, 1, late)
    _at(session, EventType.GUESS, 2, datetime(2026, 10, 7, 9, tzinfo=UTC))
    _at(session, EventType.GAME_ACTIVATED, 1, datetime(2026, 10, 8, 9, tzinfo=UTC), hard_mode=False)
    _at(session, EventType.GAME_ACTIVATED, 1, datetime(2026, 10, 9, 9, tzinfo=UTC), hard_mode=True)
    _at(session, EventType.VOTE_COUNTED, 1, datetime(2026, 10, 10, 9, tzinfo=UTC))

    history = DbHistory(session, 1, msk)
    types = (EventType.GUESS, EventType.GAME_ACTIVATED)

    assert history.my_days(*types) == {date(2026, 10, 6), date(2026, 10, 8)}
    active = [
        d for d in range(4, 12) if history.group_active_between(types, *[date(2026, 10, d)] * 2)
    ]
    assert active == [6, 7, 8]  # Oct 5's late guess is Oct 6 in Moscow; Oct 9 was a bot game
    assert history.group_active_between(types, date(2026, 10, 9), date(2026, 10, 30)) is False


def test_a_repeated_read_is_served_without_another_query(session: Session) -> None:
    events.emit(session, EventType.GUESS, events.Involved(actor_id=1, game_id=1))
    history = DbHistory(session, 1, ZoneInfo("UTC"))
    statements: list[str] = []
    bind = session.get_bind()
    listen = (bind, "before_cursor_execute")

    def count(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(*listen, count)
    try:
        first = history.mine(EventType.GUESS)
        again = history.mine(EventType.GUESS)
        history.my_days(EventType.GUESS)
        history.my_days(EventType.GUESS)
    finally:
        event.remove(*listen, count)

    assert first == again
    assert len(statements) == 2
