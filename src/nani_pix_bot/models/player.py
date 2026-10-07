from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base

# Telegram usernames are capped at 32 chars; headroom for the `@` some
# callers include.
USERNAME_LENGTH = 64
# The achievement title the player chose to show (/title):
# "<achievement key>:<tier>:<period key>", NULL = none. See
# services/achievements/titles.py.
TITLE_KEY_LENGTH = 96
# IANA zone names top out around 30 chars; headroom.
TIMEZONE_LENGTH = 64


class Player(Base):
    """A Telegram user the bot has seen — any DM, command, or message in
    the game topic, via commands/helpers/player_tracking.py. Deliberately
    broader than "has played": `/correct @username` and `/skip @username`
    resolve a typed handle against this table, and `/correct` exists
    precisely for someone who answered in plain prose without ever
    issuing a command. A row with `wins == 0` is the normal case and is
    filtered out of the leaderboard by `players.top_players`.

    `currency` is the 💠 balance, always moved together with a
    `currency_transfers` row (services/economy/wallet.py)."""

    __tablename__ = "players"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(USERNAME_LENGTH), default=None)
    wins: Mapped[int] = mapped_column(default=0)
    # The player's own IANA timezone (via /timezone) — currently only used
    # to interpret an admin's /quiethours times. NULL = never set.
    timezone: Mapped[str | None] = mapped_column(String(TIMEZONE_LENGTH), default=None)
    currency: Mapped[int] = mapped_column(default=0)
    # Telegram's first name, kept current by remember_user and used when a
    # player has no @username.
    first_name: Mapped[str | None] = mapped_column(String(USERNAME_LENGTH), default=None)
    title_key: Mapped[str | None] = mapped_column(String(TITLE_KEY_LENGTH), default=None)
