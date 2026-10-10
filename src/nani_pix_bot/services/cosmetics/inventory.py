"""Who owns and has equipped which season cosmetic (seasons spec §3)."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.cosmetics import EquippedItem, OwnedItem
from nani_pix_bot.models.enums import CosmeticSlot, ItemSource
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import players
from nani_pix_bot.services.cosmetics import looks


@dataclass(frozen=True)
class ItemGrant:
    item_id: str
    source: ItemSource
    season_id: int | None = None


class EquipRefusedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def owns(session: Session, player_id: int, item_id: str) -> bool:
    stmt = select(OwnedItem.id).where(
        OwnedItem.player_id == player_id, OwnedItem.item_id == item_id
    )
    return session.scalars(stmt.limit(1)).first() is not None


def grant(session: Session, player_id: int, request: ItemGrant) -> bool:
    item_id = request.item_id
    if registry.item(item_id) is None:
        msg = f"unknown item {item_id!r}"
        raise ValueError(msg)
    recipient = players.describe_player_id(session, player_id)
    if owns(session, player_id, item_id):
        logger.info(
            "{recipient} already owns {item_id}",
            recipient=recipient,
            recipient_id=player_id,
            item_id=item_id,
        )
        return False
    session.add(
        OwnedItem(
            player_id=player_id,
            item_id=item_id,
            source=request.source,
            season_id=request.season_id,
        )
    )
    session.flush()
    logger.info(
        "granted {item_id} to {recipient} ({source})",
        item_id=item_id,
        recipient=recipient,
        recipient_id=player_id,
        source=request.source.value,
        season_id=request.season_id,
    )
    return True


def owned(session: Session, player_id: int, slot: CosmeticSlot | None = None) -> list[ResolvedItem]:
    ids = session.scalars(
        select(OwnedItem.item_id)
        .where(OwnedItem.player_id == player_id)
        .order_by(OwnedItem.acquired_at, OwnedItem.id)
    )
    found = [r for r in (registry.item(i) for i in ids) if r is not None]
    return [r for r in found if slot is None or r.item.slot is slot]


def equip(session: Session, player_id: int, item_id: str) -> ResolvedItem:
    resolved = registry.item(item_id)
    if resolved is None:
        raise EquipRefusedError("unknown")
    if not owns(session, player_id, item_id):
        raise EquipRefusedError("not_owned")
    row = session.get(EquippedItem, (player_id, resolved.item.slot))
    if row is None:
        session.add(EquippedItem(player_id=player_id, slot=resolved.item.slot, item_id=item_id))
    else:
        row.item_id = item_id
    session.flush()
    logger.info("equipped {item_id} as {slot}", item_id=item_id, slot=resolved.item.slot.value)
    return resolved


def unequip(session: Session, player_id: int, slot: CosmeticSlot) -> None:
    row = session.get(EquippedItem, (player_id, slot))
    if row is not None:
        session.delete(row)
        session.flush()
        logger.info("unequipped {slot}", slot=slot.value, item_id=row.item_id)


def equipped(session: Session, player_id: int, slot: CosmeticSlot) -> ResolvedItem | None:
    row = session.get(EquippedItem, (player_id, slot))
    if row is None:
        return None
    resolved = registry.item(row.item_id)
    if resolved is None or looks.problems(resolved):
        logger.warning(
            "equipped {item_id} no longer resolves — ignoring it",
            item_id=row.item_id,
            player_id=player_id,
            slot=slot.value,
        )
        return None
    return resolved


def look(session: Session, player_id: int, slot: CosmeticSlot) -> str | None:
    resolved = equipped(session, player_id, slot)
    return None if resolved is None else resolved.item.look
