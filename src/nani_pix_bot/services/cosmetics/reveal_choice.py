"""Which reveal effect a game gets (seasons spec §3 `host_transition`): the
starter's equipped transition, else a random one."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CosmeticSlot, RevealEffect
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.reveal import store


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
