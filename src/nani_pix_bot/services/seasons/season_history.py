"""The event history one season's objectives read (seasons spec §5/§6, rule
D1 of the Phase 4 plan): its games' events, plus game-less events (tips)
during its dates. Phase 5's season achievements reuse it."""

from datetime import datetime

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType, SeasonStatus
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services import settings
from nani_pix_bot.services.achievements.history import DbHistory
from nani_pix_bot.services.events import LoggedEvent, as_utc
from nani_pix_bot.services.seasons import schedule


def _naive(at: datetime) -> datetime:
    """UTC without tzinfo, how the DATETIME column stores it."""
    return as_utc(at).replace(tzinfo=None)


SEASON_OWNED = (
    EventType.ITEM_GAINED,
    EventType.TRACK_COMPLETED,
    EventType.SEASON_ENDED,
    EventType.GOAL_REACHED,
)


def scope(season: SeasonSchedule) -> ColumnElement[bool]:
    tagged = EventLog.game_id.in_(select(Game.id).where(Game.season_id == season.id))
    start = _naive(season.started_at or season.start_at)
    during = and_(
        EventLog.game_id.is_(None),
        EventLog.occurred_at >= start,
        EventLog.occurred_at < _naive(season.end_at),
    )
    # Season-owned events carry their season in data and may land after end_at
    # (a closing season's last pass, the end itself); readers filter by season_id.
    owned = EventLog.event_type.in_(SEASON_OWNED)
    return or_(tagged, during, owned)


def season_of_event(session: Session, event: LoggedEvent) -> SeasonSchedule | None:
    """The season an event belongs to (E5): its data, else its game, else (no
    game) the active one — and only while that season still grants."""
    season_id = event.data.get("season_id")
    if season_id is None and event.game_id is not None:
        game = session.get(Game, event.game_id)
        season_id = game.season_id if game is not None else None
    if season_id is not None:
        season = session.get(SeasonSchedule, season_id)
    elif event.game_id is None:
        season = schedule.active(session)
    else:
        season = None
    if season is None or season.status not in (SeasonStatus.ACTIVE, SeasonStatus.CLOSING):
        return None
    return season


def for_player(session: Session, season: SeasonSchedule, player_id: int) -> DbHistory:
    tz = settings.get_group_timezone(session)
    return DbHistory(session, player_id, tz, scope=scope(season))
