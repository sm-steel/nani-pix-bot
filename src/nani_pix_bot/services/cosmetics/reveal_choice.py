"""Which reveal effect a game gets (seasons spec §3 `host_transition`): the
starter's equipped transition, else a random one. Also which win overlays to
draw ahead at game start, so a win doesn't pay for them."""

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.cosmetics import EquippedItem
from nani_pix_bot.models.enums import CosmeticSlot, RevealEffect
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.reveal import overlays, store


def overlays_in_use(session: Session) -> tuple[str, ...]:
    """The registered win overlays anyone has equipped, besides the default
    (which is always drawn ahead), sorted: whoever wins, theirs is ready."""
    item_ids = session.scalars(
        select(EquippedItem.item_id).where(EquippedItem.slot == CosmeticSlot.WIN_EFFECT)
    )
    looks = {resolved.item.look for i in item_ids if (resolved := registry.item(i)) is not None}
    in_use = looks & overlays.WIN_OVERLAYS.keys() - {overlays.DEFAULT_OVERLAY}
    return tuple(sorted(in_use))


def effect_for(session: Session, starter_id: int) -> RevealEffect:
    look = inventory.look(session, starter_id, CosmeticSlot.HOST_TRANSITION)
    if look is None:
        return store.pick_effect()
    try:
        return RevealEffect[look]
    except KeyError:
        logger.warning(
            "unknown reveal transition {look!r} — picking a random effect instead", look=look
        )
        return store.pick_effect()
