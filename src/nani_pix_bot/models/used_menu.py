from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base


class UsedMenu(Base):
    """A menu message whose one action already ran (issue #479): a second tap on
    it, even a quick double tap, is refused. See services/used_menus.py. The
    key is all it holds: nothing reads when a menu was used."""

    __tablename__ = "used_menus"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
