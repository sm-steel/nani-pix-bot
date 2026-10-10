"""Name flair: the emoji a player equipped, appended to a name shown in the group.

Text only — Pillow cards and the reveal badge keep the plain name.
"""

from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.services.cosmetics import inventory


def named(session: Session, user_id: int, name: str) -> str:
    """`name` plus the player's equipped flair; `name` itself when there is none."""
    emoji = inventory.look(session, user_id, CosmeticSlot.NAME_FLAIR)
    return name if emoji is None else f"{name} {emoji}"
