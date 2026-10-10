"""A season's way through its states (seasons spec §1): scheduled → active
at start_at, active → closing at end_at, closing → ended once none of its
tagged games is still running. `advance` does every step that's due in one
pass, which is also the catch-up after downtime."""

from dataclasses import dataclass
from datetime import datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType, GameStatus, OutboxKind, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonResult, SeasonSchedule
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.events import LoggedEvent, as_utc
from nani_pix_bot.services.seasons import schedule, xp

_RUNNING = (GameStatus.ACTIVE, GameStatus.VOTING)


@dataclass(frozen=True)
class Transition:
    season_id: int
    run_id: str
    status: SeasonStatus


def _running_games(session: Session, season_id: int) -> int:
    stmt = select(func.count(Game.id)).where(Game.season_id == season_id, Game.status.in_(_RUNNING))
    return session.scalar(stmt) or 0


def _move(row: SeasonSchedule, status: SeasonStatus) -> Transition:
    row.status = status
    logger.info(
        "season {run_id} is now {status}",
        run_id=row.run_id,
        status=status.value,
        season_id=row.id,
    )
    return Transition(row.id, row.run_id, status)


def _finalize(session: Session, row: SeasonSchedule, now: datetime) -> Transition:
    for placed in xp.standings(session, row.id):
        session.add(
            SeasonResult(
                season_id=row.id, player_id=placed.player_id, rank=placed.rank, xp=placed.xp
            )
        )
    row.ended_at = now
    session.flush()
    outbox.enqueue_season(session, OutboxKind.SEASON_END, row.id)
    return _move(row, SeasonStatus.ENDED)


def advance(session: Session, now: datetime) -> list[Transition]:
    row = schedule.current(session)
    done: list[Transition] = []
    if row is None:
        return done
    if row.status == SeasonStatus.SCHEDULED and as_utc(row.start_at) <= now:
        row.started_at = now
        done.append(_move(row, SeasonStatus.ACTIVE))
        outbox.enqueue_season(session, OutboxKind.SEASON_START, row.id)
    if row.status == SeasonStatus.ACTIVE and as_utc(row.end_at) <= now:
        done.append(_move(row, SeasonStatus.CLOSING))
    if row.status == SeasonStatus.CLOSING:
        running = _running_games(session, row.id)
        if running:
            logger.info(
                "season {run_id} waits for {running} running game(s) before it ends",
                run_id=row.run_id,
                running=running,
                season_id=row.id,
            )
        else:
            done.append(_finalize(session, row, now))
    return done


def next_boundary(session: Session) -> datetime | None:
    """When advance next has something to do on the clock; None while
    nothing is open or a closing season waits for its games (their end
    triggers it — see on_game_ended)."""
    row = schedule.current(session)
    if row is None:
        return None
    if row.status == SeasonStatus.SCHEDULED:
        return as_utc(row.start_at)
    if row.status == SeasonStatus.ACTIVE:
        return as_utc(row.end_at)
    return None


def on_game_ended(session: Session, event: LoggedEvent) -> None:
    if event.event_type not in (EventType.GAME_WON, EventType.GAME_UNSOLVED):
        return
    game = session.get(Game, event.game_id) if event.game_id is not None else None
    if game is None or game.season_id is None:
        return
    row = session.get(SeasonSchedule, game.season_id)
    if row is not None and row.status == SeasonStatus.CLOSING:
        advance(session, event.occurred_at)


def podium(session: Session, season_id: int) -> list[SeasonResult]:
    stmt = (
        select(SeasonResult)
        .where(SeasonResult.season_id == season_id)
        .order_by(SeasonResult.rank, SeasonResult.player_id)
    )
    return list(session.scalars(stmt))
