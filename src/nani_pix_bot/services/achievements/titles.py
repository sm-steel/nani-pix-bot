"""Prestige titles (spec §4): which grants confer one, choosing one, and
rendering a stored choice."""

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.achievements import catalogue, names
from nani_pix_bot.services.achievements.definitions import grants_title


def _confers(grant: AchievementGrant) -> bool:
    try:
        defn = catalogue.get(grant.key)
    except KeyError:
        return False
    return grants_title(defn, grant.tier)


def eligible(session: Session, player_id: int) -> list[AchievementGrant]:
    """The player's grants that confer a title, newest first."""
    stmt = (
        select(AchievementGrant)
        .where(AchievementGrant.player_id == player_id)
        .order_by(AchievementGrant.granted_at.desc(), AchievementGrant.id.desc())
    )
    return [g for g in session.scalars(stmt) if _confers(g)]


def _encode(grant: AchievementGrant) -> str:
    return f"{grant.key}:{grant.tier}:{grant.period_key}"


def choose(session: Session, player_id: int, grant_id: int | None) -> bool:
    """Show the title of one of the player's own grants (None clears it).
    False when the player or the grant can't be used."""
    player = session.get(Player, player_id)
    if player is None:
        return False
    if grant_id is None:
        player.title_key = None
        logger.info("cleared their title")
        return True
    grant = session.get(AchievementGrant, grant_id)
    if grant is None or grant.player_id != player_id or not _confers(grant):
        logger.warning(
            "tried to take the title of grant {grant_id}, which isn't theirs to take",
            grant_id=grant_id,
        )
        return False
    player.title_key = _encode(grant)
    logger.info("chose the title {title_key}", title_key=player.title_key)
    return True


def text(title_key: str | None, lang: str) -> str | None:
    """A stored choice as display text; None for no title or a retired key."""
    if not title_key:
        return None
    key, tier, period_key = [*title_key.split(":"), "", "", ""][:3]
    try:
        defn = catalogue.get(key)
    except KeyError:
        logger.warning("stored title {title_key!r} names no achievement", title_key=title_key)
        return None
    return names.title(defn, int(tier or 1), period_key, lang)
