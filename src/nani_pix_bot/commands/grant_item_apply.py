"""The grant itself, shared by `/grantitem @user <item id>` and a pick from
the `/grantitem <item id>` menu (grant_item_picker.py)."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import ItemSource
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant


def give(session: Session, player_id: int, resolved: ResolvedItem, lang: str) -> str:
    """Grant the item to the player; the reply (granted, or already owned)."""
    item_id = resolved.item_id
    created = inventory.grant(session, player_id, ItemGrant(item_id, ItemSource.ADMIN))
    if not created:
        logger.warning(
            "/grantitem refused: {target} already owns {item_id}",
            target=players.describe_player_id(session, player_id),
            target_id=player_id,
            item_id=item_id,
        )
    return i18n.t(
        "grant_item.granted" if created else "grant_item.already_owned",
        lang,
        item=resolved.item.name(lang),
        item_id=item_id,
        player=players.display_name(session, player_id),
    )
