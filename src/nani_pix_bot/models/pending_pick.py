from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.cosmetics import ITEM_ID_LENGTH


class PendingPick(Base):
    """The item a `/grantitem <item id>` player menu will grant (issue #479),
    when the item id is too long to ride in the buttons' callback data: the
    buttons carry this row's id instead. See services/pending_picks.py. The
    row goes once the menu grants or is cancelled."""

    __tablename__ = "pending_picks"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(String(ITEM_ID_LENGTH))
