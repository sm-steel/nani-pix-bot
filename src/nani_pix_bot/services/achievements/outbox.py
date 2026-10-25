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


def enqueue_season(session: Session, kind: OutboxKind, season_id: int) -> AnnouncementOutbox:
    return _add(session, AnnouncementOutbox(kind=kind, payload={"season_id": season_id}))


def enqueue_tier(
    session: Session, season_id: int, player_id: int, granted: list[dict]
) -> AnnouncementOutbox:
    """One DM for everything a sync granted: [{"tier", "premium", "new"}]."""
    payload = {"season_id": season_id, "player_id": player_id, "granted": granted}
    return _add(session, AnnouncementOutbox(kind=OutboxKind.SEASON_TIER, payload=payload))


def enqueue_pass_done(session: Session, season_id: int, player_id: int) -> AnnouncementOutbox:
    payload = {"season_id": season_id, "player_id": player_id}
    return _add(session, AnnouncementOutbox(kind=OutboxKind.SEASON_COMPLETE, payload=payload))


def enqueue_objective(
    session: Session, season_id: int, player_id: int, key: str
) -> AnnouncementOutbox:
    """The DM for one completed season objective (services/seasons/objectives.py)."""
    payload = {"season_id": season_id, "player_id": player_id, "objective": key}
    return _add(session, AnnouncementOutbox(kind=OutboxKind.SEASON_OBJECTIVE, payload=payload))


def enqueue_hex_notice(session: Session, hex_id: int, notice: str) -> AnnouncementOutbox:
    """The topic post for a hex going off: notice is "triggered" (the only one; an
    omikuji self-prank posts its Kyō card straight from /omikuji)."""
    payload = {"hex_id": hex_id, "notice": notice}
    return _add(session, AnnouncementOutbox(kind=OutboxKind.HEX_NOTICE, payload=payload))


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
