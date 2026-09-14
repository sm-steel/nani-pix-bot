from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base

# Telegram usernames are capped at 32 chars; headroom for the `@` some
# callers include.
USERNAME_LENGTH = 64


class Player(Base):
    """A Telegram user the bot has seen — any DM, command, or message in
    the game topic, via commands/helpers/player_tracking.py. Deliberately
    broader than "has played": `/correct @username` and `/skip @username`
    resolve a typed handle against this table, and `/correct` exists
    precisely for someone who answered in plain prose without ever
    issuing a command. A row with `wins == 0` is the normal case and is
    filtered out of the leaderboard by `players.top_players`."""

    __tablename__ = "players"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(USERNAME_LENGTH), default=None)
    wins: Mapped[int] = mapped_column(default=0)
