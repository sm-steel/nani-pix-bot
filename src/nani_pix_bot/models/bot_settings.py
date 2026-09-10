from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base

LANGUAGE_LENGTH = 8


class BotSettings(Base):
    """Single row (id=1) of bot-wide settings — currently just the
    admin-configurable RU/EN language. Same singleton pattern as
    TurnState."""

    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    language: Mapped[str] = mapped_column(String(LANGUAGE_LENGTH), default="EN")
