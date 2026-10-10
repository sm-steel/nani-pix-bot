"""Scheduling rules (seasons spec §1): history kept, at most one open
season, a run that ever started is never scheduled again, dates movable
(an end at/before now on a running season ends it at once)."""

import enum
from dataclasses import dataclass
from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import OutboxKind, SeasonStatus
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.events import as_utc

OPEN_STATUSES = (SeasonStatus.SCHEDULED, SeasonStatus.ACTIVE, SeasonStatus.CLOSING)


class Refusal(enum.StrEnum):
    UNKNOWN_RUN = "unknown_run"
    ALREADY_HELD = "already_held"
    BUSY = "busy"
    BAD_WINDOW = "bad_window"
    NOTHING_OPEN = "nothing_open"
    NOT_SCHEDULED = "not_scheduled"
    ALREADY_ENDING = "already_ending"


class ScheduleRefusedError(Exception):
    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


def current(session: Session) -> SeasonSchedule | None:
    stmt = (
        select(SeasonSchedule)
        .where(SeasonSchedule.status.in_(OPEN_STATUSES))
        .order_by(SeasonSchedule.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def active(session: Session) -> SeasonSchedule | None:
    row = current(session)
    return row if row is not None and row.status == SeasonStatus.ACTIVE else None


def active_id(session: Session) -> int | None:
    row = active(session)
    return None if row is None else row.id


def held_run_ids(session: Session) -> set[str]:
    stmt = select(SeasonSchedule.run_id).where(SeasonSchedule.started_at.is_not(None))
    return set(session.scalars(stmt))


def available_runs(session: Session) -> list[SeasonRun]:
    held = held_run_ids(session)
    return [run for run_id, run in sorted(registry.all_runs().items()) if run_id not in held]


def history(session: Session, limit: int = 10) -> list[SeasonSchedule]:
    stmt = select(SeasonSchedule).order_by(SeasonSchedule.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def _check_window(start_at: datetime, end_at: datetime, now: datetime) -> None:
    if end_at <= start_at or end_at <= now:
        raise ScheduleRefusedError(Refusal.BAD_WINDOW)


def _open_or_refuse(session: Session) -> SeasonSchedule:
    row = current(session)
    if row is None:
        raise ScheduleRefusedError(Refusal.NOTHING_OPEN)
    return row


@dataclass(frozen=True)
class ScheduleRequest:
    run_id: str
    start_at: datetime
    end_at: datetime
    admin_id: int


def schedule(session: Session, request: ScheduleRequest, now: datetime) -> SeasonSchedule:
    run_id, start_at, end_at = request.run_id, request.start_at, request.end_at
    if registry.get(run_id) is None:
        raise ScheduleRefusedError(Refusal.UNKNOWN_RUN)
    if run_id in held_run_ids(session):
        raise ScheduleRefusedError(Refusal.ALREADY_HELD)
    if current(session) is not None:
        raise ScheduleRefusedError(Refusal.BUSY)
    _check_window(start_at, end_at, now)
    row = SeasonSchedule(
        run_id=run_id,
        start_at=start_at,
        end_at=end_at,
        status=SeasonStatus.SCHEDULED,
        created_by=request.admin_id,
    )
    session.add(row)
    session.flush()
    outbox.enqueue_season(session, OutboxKind.SEASON_TEASER, row.id)
    logger.info(
        "scheduled season {run_id} for {start} to {end}",
        run_id=run_id,
        start=start_at.isoformat(),
        end=end_at.isoformat(),
        season_id=row.id,
    )
    return row


def set_start(session: Session, start_at: datetime, now: datetime) -> SeasonSchedule:
    row = _open_or_refuse(session)
    if row.status != SeasonStatus.SCHEDULED:
        raise ScheduleRefusedError(Refusal.NOT_SCHEDULED)
    _check_window(start_at, as_utc(row.end_at), now)
    row.start_at = start_at
    outbox.enqueue_season(session, OutboxKind.SEASON_TEASER, row.id)
    logger.info(
        "moved season start to {start}",
        start=start_at.isoformat(),
        season_id=row.id,
        run_id=row.run_id,
    )
    return row


def set_end(session: Session, end_at: datetime, now: datetime) -> SeasonSchedule:
    row = _open_or_refuse(session)
    if row.status == SeasonStatus.CLOSING:
        raise ScheduleRefusedError(Refusal.ALREADY_ENDING)
    if row.status == SeasonStatus.SCHEDULED:
        _check_window(as_utc(row.start_at), end_at, now)
        outbox.enqueue_season(session, OutboxKind.SEASON_TEASER, row.id)
    else:
        end_at = max(end_at, now)  # running: an end in the past means "now"
    row.end_at = end_at
    logger.info(
        "moved season end to {end}",
        end=end_at.isoformat(),
        season_id=row.id,
        run_id=row.run_id,
    )
    return row


def cancel(session: Session, now: datetime) -> SeasonSchedule:
    row = _open_or_refuse(session)
    if row.status != SeasonStatus.SCHEDULED:
        raise ScheduleRefusedError(Refusal.NOT_SCHEDULED)
    row.status = SeasonStatus.CANCELLED
    row.ended_at = now
    logger.info("cancelled season {run_id}", run_id=row.run_id, season_id=row.id)
    return row
