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
    # The Telegram message_id of whatever "current image" (a pixelated
    # stage or a reveal) is currently pinned in the game topic — a
    # singleton pointer rather than a per-Game column since it's meant
    # to persist across games (see services/settings/bot_settings.py's
    # get/set_pinned_message_id and jobs/timers.py's post_current_image).
    pinned_message_id: Mapped[int | None] = mapped_column(default=None)
    # Admin-gated via /setautostart (see commands/setautostart.py) —
    # whether the bot may start a game itself (idle auto-start, or
    # "overthrow" right after a game concludes). Independent of, and
    # checked in addition to, games_enabled above. Defaults False:
    # unlike games_enabled (a safety valve for something that's always
    # been possible), bot-initiated games are new behavior an admin
    # should opt into.
    autostart_enabled: Mapped[bool] = mapped_column(default=False)
