"""Prestige titles (spec §4): which grants confer one, choosing one, and
rendering a stored choice."""

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services.achievements import catalogue, names
from nani_pix_bot.services.achievements.definitions import grants_title
from nani_pix_bot.services.cosmetics import inventory

ITEM_PREFIX = "item:"
# A season title item is stored as `item:<item id>` in Player.title_key (String(96))
# and offered as `title:i:<item id>` callback data (Telegram: 64 bytes). The tighter
# bound is the callback: 64 - len("title:i:") = 56. Real ids are `<run_id>/title.<name>`,
# well under that; longer ones are never offered or accepted.
MAX_ITEM_ID_LENGTH = 56


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


def item_name(resolved: ResolvedItem, lang: str) -> str:
    """The item's display name; season data keys languages upper-case."""
    return resolved.item.name(lang.upper())


def item_titles(session: Session, player_id: int) -> list[ResolvedItem]:
    """Season title items the player owns and can pick."""
    found = inventory.owned(session, player_id, CosmeticSlot.TITLE)
    usable = [r for r in found if len(r.item_id) <= MAX_ITEM_ID_LENGTH]
    for r in found:
        if r not in usable:
            logger.warning("title item {item_id} id is too long to offer", item_id=r.item_id)
    return usable


def _takeable_item(session: Session, player_id: int, item_id: str) -> bool:
    resolved = registry.item(item_id)
    if resolved is None or resolved.item.slot is not CosmeticSlot.TITLE:
        return False
    if len(item_id) > MAX_ITEM_ID_LENGTH:
        return False
    return inventory.owns(session, player_id, item_id)


def choose_item(session: Session, player_id: int, item_id: str) -> bool:
    """Show an owned season title item. False when it isn't theirs to take."""
    player = session.get(Player, player_id)
    if player is None or not _takeable_item(session, player_id, item_id):
        logger.warning(
            "tried to take the title item {item_id}, which isn't theirs to take",
            item_id=item_id,
        )
        return False
    player.title_key = f"{ITEM_PREFIX}{item_id}"
    logger.info("chose the title {title_key}", title_key=player.title_key)
    return True


def text(title_key: str | None, lang: str) -> str | None:
    """A stored choice as display text; None for no title or a retired key."""
    if not title_key:
        return None
    if title_key.startswith(ITEM_PREFIX):
        resolved = registry.item(title_key.removeprefix(ITEM_PREFIX))
        if resolved is None:
            logger.warning("stored title {title_key!r} names no item", title_key=title_key)
            return None
        return item_name(resolved, lang)
    key, tier, period_key = [*title_key.split(":"), "", "", ""][:3]
    try:
        defn = catalogue.get(key)
    except KeyError:
        logger.warning("stored title {title_key!r} names no achievement", title_key=title_key)
        return None
    return names.title(defn, int(tier or 1), period_key, lang)
