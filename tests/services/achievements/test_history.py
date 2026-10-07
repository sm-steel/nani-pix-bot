from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType
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
