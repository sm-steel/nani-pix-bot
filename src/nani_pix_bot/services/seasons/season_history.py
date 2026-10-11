"""The event history one season's objectives read (seasons spec §5/§6, rule
D1 of the Phase 4 plan): its games' events, plus game-less events (tips)
during its dates. Phase 5's season achievements reuse it."""

from datetime import datetime

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services import settings
from nani_pix_bot.services.achievements.history import DbHistory
from nani_pix_bot.services.events import as_utc


def _naive(at: datetime) -> datetime:
    """UTC without tzinfo, how the DATETIME column stores it."""
    return as_utc(at).replace(tzinfo=None)


def scope(season: SeasonSchedule) -> ColumnElement[bool]:
    tagged = EventLog.game_id.in_(select(Game.id).where(Game.season_id == season.id))
    start = _naive(season.started_at or season.start_at)
    during = and_(
        EventLog.game_id.is_(None),
        EventLog.occurred_at >= start,
        EventLog.occurred_at < _naive(season.end_at),
    )
    return or_(tagged, during)


def for_player(session: Session, season: SeasonSchedule, player_id: int) -> DbHistory:
    tz = settings.get_group_timezone(session)
    return DbHistory(session, player_id, tz, scope=scope(season))
