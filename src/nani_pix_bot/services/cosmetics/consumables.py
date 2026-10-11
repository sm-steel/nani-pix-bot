"""Consumables a player holds a count of (seasons spec §3): clue tokens."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.cosmetics import Consumable
from nani_pix_bot.models.enums import ConsumableKind


def _row(session: Session, player_id: int, kind: ConsumableKind) -> Consumable:
    row = session.get(Consumable, (player_id, kind))
    if row is None:
        row = Consumable(player_id=player_id, kind=kind, count=0)
        session.add(row)
    return row


def count(session: Session, player_id: int, kind: ConsumableKind) -> int:
    row = session.get(Consumable, (player_id, kind))
    return 0 if row is None else row.count


def add(session: Session, player_id: int, kind: ConsumableKind, n: int = 1, why: str = "") -> int:
    row = _row(session, player_id, kind)
    row.count += n
    session.flush()
    logger.info(
        "got {n} {kind} ({why}): now {count}",
        n=n,
        kind=kind.value,
        why=why,
        count=row.count,
        recipient_id=player_id,
    )
    return row.count


def take(session: Session, player_id: int, kind: ConsumableKind) -> bool:
    row = session.get(Consumable, (player_id, kind))
    if row is None or row.count <= 0:
        return False
    row.count -= 1
    session.flush()
    logger.info("used a {kind}: {count} left", kind=kind.value, count=row.count)
    return True
