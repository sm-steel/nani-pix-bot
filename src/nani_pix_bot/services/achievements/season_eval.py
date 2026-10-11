"""Evaluating per-season achievements (Kind.SEASON, seasons spec §6): against
the event's season only, once per season (period_key = its run_id)."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services.achievements.definitions import Definition, SeasonHistory, reached
from nani_pix_bot.services.achievements.history import DbHistory
from nani_pix_bot.services.events import LoggedEvent, as_utc
from nani_pix_bot.services.seasons import season_history


@dataclass
class _Scoped:
    """DbHistory plus the season facts season progress reads (SeasonHistory)."""

    history: DbHistory
    player_id: int
    tz: ZoneInfo
    season_id: int
    run: SeasonRun
    first_day: date
    last_day: date

    # Forwarded one by one: ty doesn't count __getattr__ delegation toward a protocol.
    def mine(self, *types: EventType) -> list[LoggedEvent]:
        return self.history.mine(*types)

    def about_me(self, *types: EventType) -> list[LoggedEvent]:
        return self.history.about_me(*types)

    def group(self, *types: EventType) -> list[LoggedEvent]:
        return self.history.group(*types)

    def my_days(self, *types: EventType) -> set[date]:
        return self.history.my_days(*types)

    def group_active_between(self, types: tuple[EventType, ...], first: date, last: date) -> bool:
        return self.history.group_active_between(types, first, last)


def _local_day(history: DbHistory, at: datetime) -> date:
    return as_utc(at).astimezone(history.tz).date()


def scoped_history(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int
) -> SeasonHistory:
    history = season_history.for_player(session, season, player_id)
    start = season.started_at or season.start_at
    return _Scoped(
        history,
        history.player_id,
        history.tz,
        season.id,
        run,
        _local_day(history, start),
        _local_day(history, as_utc(season.end_at) - timedelta(microseconds=1)),
    )


def counts_in(defn: Definition, run: SeasonRun) -> bool:
    """Whether `defn` counts in a season of `run`: a run-bound one only in its own."""
    return defn.run_id is None or defn.run_id == run.run_id


def evaluate(
    session: Session, defn: Definition, event: LoggedEvent, player_id: int
) -> list[AchievementGrant]:
    # Imported here: engine imports this module, and grant lives in engine.
    from nani_pix_bot.services.achievements import engine

    season = season_history.season_of_event(session, event)
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None or not counts_in(defn, run):
        logger.debug(
            "{achievement}: no granting season for event {event_id}",
            achievement=defn.key,
            event_id=event.id,
        )
        return []
    top = reached(defn, defn.progress(scoped_history(session, season, run, player_id)))
    granted = []
    for tier in range(1, top + 1):
        # Re-read every time, as the engine does: a paid reward may re-enter it.
        if tier not in engine.held_tiers(session, player_id, defn.key, period_key=run.run_id):
            request = engine.GrantRequest(player_id, defn.key, tier, run.run_id)
            granted.append(engine.grant(session, request, batch_id=event.id))
    return granted
