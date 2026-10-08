"""The single reveal slot: reserve at game start, fill when the worker is
done, clear after the reveal is sent. Every write after `reserve` is guarded
by `game_id`, so a render that finishes after a newer game took the slot
can't overwrite it (spec: "stale render")."""

import random
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import RevealEffect, RevealStatus
from nani_pix_bot.models.reveal_video import RevealVideo

SLOT = 1
_IMAGES = ("a", "b")


def pick_effect(rng: random.Random | None = None) -> RevealEffect:
    return (rng or random).choice(list(RevealEffect))


def pick_image(rng: random.Random | None = None) -> str:
    return (rng or random).choice(_IMAGES)


def load(session: Session) -> RevealVideo | None:
    return session.get(RevealVideo, SLOT)


def reserve(session: Session, game_id: int, effect: RevealEffect, image_choice: str | None) -> None:
    row = load(session)
    if row is None:
        row = RevealVideo(slot=SLOT)
        session.add(row)
    row.game_id = game_id
    row.effect = effect
    row.image_choice = image_choice
    row.status = RevealStatus.PENDING
    row.part1_ts = None
    row.join_offset = None
    row.created_at = datetime.now(UTC)
    row.ready_at = None
    session.flush()
    logger.info(
        "reveal effect {effect}{image} chosen",
        effect=effect.value,
        image=f" (image {image_choice})" if image_choice else "",
        game_id=game_id,
    )


def _owned(session: Session, game_id: int) -> RevealVideo | None:
    row = load(session)
    return row if row is not None and row.game_id == game_id else None


def mark_ready(session: Session, game_id: int, part1_ts: bytes, join_offset: float) -> bool:
    row = _owned(session, game_id)
    if row is None:
        return False
    row.part1_ts = part1_ts
    row.join_offset = join_offset
    row.status = RevealStatus.READY
    row.ready_at = datetime.now(UTC)
    return True


def mark_failed(session: Session, game_id: int) -> bool:
    row = _owned(session, game_id)
    if row is None:
        return False
    row.status = RevealStatus.FAILED
    return True


def clear(session: Session, game_id: int) -> bool:
    row = _owned(session, game_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
