from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base


class Player(Base):
    """A Telegram user known to the game (has started or won at least once)."""

    __tablename__ = "players"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(default=None)
    wins: Mapped[int] = mapped_column(default=0)
