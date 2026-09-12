"""Everything that touches only the Player table: lookup/creation,
case-insensitive username lookup, and the /leaderboard query. Win
increments themselves live in services/game/state.py's _win() (the
only place that mutates a Game/Player pair together)."""

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player


def get_or_create_player(
    session: Session, telegram_user_id: int, *, username: str | None = None
) -> Player:
    """Look up a player, creating the row if this is their first time. On
    an existing row, opportunistically refreshes `username`."""
    player = session.get(Player, telegram_user_id)
    if player is None:
        player = Player(telegram_user_id=telegram_user_id, username=username)
        session.add(player)
    elif username is not None:
        player.username = username
    return player


def find_player_by_username(session: Session, username: str) -> Player | None:
    """Case-insensitive lookup by the opportunistically-cached username —
    used by /correct, which takes a plain @username rather than a reply."""
    stmt = select(Player).where(func.lower(Player.username) == username.lower())
    return session.scalars(stmt).first()


def top_players(session: Session, *, limit: int) -> list[Player]:
    """Players with at least one win, ordered by wins descending."""
    stmt = select(Player).where(Player.wins > 0).order_by(Player.wins.desc()).limit(limit)
    players = list(session.scalars(stmt))
    logger.debug("Leaderboard query returned {} player(s) (limit {})", len(players), limit)
    return players
