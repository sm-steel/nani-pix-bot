"""Win-count bookkeeping and the /leaderboard query. Win increments
themselves live in services/game.py (the only place that mutates a
Game/Player pair together) — this module is read-side only."""

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player


def top_players(session: Session, *, limit: int) -> list[Player]:
    """Players with at least one win, ordered by wins descending."""
    stmt = select(Player).where(Player.wins > 0).order_by(Player.wins.desc()).limit(limit)
    players = list(session.scalars(stmt))
    logger.debug("Leaderboard query returned {} player(s) (limit {})", len(players), limit)
    return players
