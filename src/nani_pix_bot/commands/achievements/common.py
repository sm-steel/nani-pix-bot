"""Shared by the group summary, the DM browser and the deep link."""

from sqlalchemy.orm import Session
from telegram.ext import ContextTypes

from nani_pix_bot.services import players

DEEP_LINK_PREFIX = "ach_"


def resolve_owner(session: Session, viewer_id: int, args: list[str]) -> int | None:
    """Whose achievements to show: `@user` from the args, else the viewer.
    None when the named player is unknown."""
    if not args:
        return viewer_id
    player = players.find_player_by_username(session, args[0].lstrip("@"))
    return None if player is None else player.telegram_user_id


def dm_link(context: ContextTypes.DEFAULT_TYPE, owner_id: int) -> str | None:
    """The deep link that opens owner_id's achievements in the bot's DM (None
    until app.py's _post_init has learned the bot's username)."""
    username = context.bot_data.get("bot_username")
    return f"https://t.me/{username}?start={DEEP_LINK_PREFIX}{owner_id}" if username else None
