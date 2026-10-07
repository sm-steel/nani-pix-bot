"""The announcement outbox's service side: queue, list, settle."""

from collections.abc import Sequence
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import OutboxKind

MAX_ATTEMPTS = 3


def _add(session: Session, row: AnnouncementOutbox) -> AnnouncementOutbox:
    session.add(row)
    session.flush()
    logger.debug("queued {kind} announcement {outbox_id}", kind=row.kind, outbox_id=row.id)
    return row


def enqueue_unlock(session: Session, grant_id: int, batch_id: int | None) -> AnnouncementOutbox:
    row = AnnouncementOutbox(
        kind=OutboxKind.UNLOCK, payload={"grant_id": grant_id}, batch_id=batch_id
    )
    return _add(session, row)


def enqueue_period_summary(
    session: Session, period_type: str, period_key: str
) -> AnnouncementOutbox:
    payload = {"period_type": period_type, "period_key": period_key}
    return _add(session, AnnouncementOutbox(kind=OutboxKind.PERIOD_SUMMARY, payload=payload))


def pending(session: Session, limit: int) -> list[AnnouncementOutbox]:
    stmt = (
        select(AnnouncementOutbox)
        .where(AnnouncementOutbox.posted_at.is_(None), AnnouncementOutbox.attempts < MAX_ATTEMPTS)
        .order_by(AnnouncementOutbox.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def _rows(session: Session, ids: Sequence[int]) -> list[AnnouncementOutbox]:
    return list(session.scalars(select(AnnouncementOutbox).where(AnnouncementOutbox.id.in_(ids))))


def mark_posted(session: Session, ids: Sequence[int]) -> None:
    now = datetime.now(UTC)
    for row in _rows(session, ids):
        row.posted_at = now


def mark_failed(session: Session, ids: Sequence[int]) -> None:
    for row in _rows(session, ids):
        row.attempts += 1
        if row.attempts >= MAX_ATTEMPTS:
            logger.error(
                "announcement {outbox_id} failed {attempts} times — giving up",
                outbox_id=row.id,
                attempts=row.attempts,
            )
