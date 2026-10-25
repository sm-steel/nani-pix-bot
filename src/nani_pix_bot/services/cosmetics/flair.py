"""Name flair: the emoji a player equipped, appended to a name shown in the group.

Text only — Pillow cards and the reveal badge keep the plain name.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.seasons import hex_marks


def named(session: Session, user_id: int, name: str, now: datetime | None = None) -> str:
    """`name` plus the player's equipped flair; `name` itself when there is none.
    A hexed player also gets a skull, or goes faceless under Nopperabo."""
    emoji = inventory.look(session, user_id, CosmeticSlot.NAME_FLAIR)
    return hex_marks.marked_name(session, user_id, name, emoji, now or datetime.now(UTC))
