from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base

LANGUAGE_LENGTH = 8


class BotSettings(Base):
    """Single row (id=1) of bot-wide settings — the admin-configurable
    RU/EN language, and whether starting new games is currently allowed
    (see /setgamesenabled). Same singleton pattern as TurnState."""

    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    language: Mapped[str] = mapped_column(String(LANGUAGE_LENGTH), default="EN")
    # Lets an admin lock out starting *new* games (e.g. while mid
    # stage-config tuning) without affecting a game already in progress.
    games_enabled: Mapped[bool] = mapped_column(default=True)
