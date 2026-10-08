"""Shared by the group summary, the DM browser, the deep link, /standings
and /title."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings

DEEP_LINK_PREFIX = "ach_"
MAX_ID = 2**63 - 1  # BigInteger ceiling: anything larger would overflow the DB
PREFIX = "ach:"  # callback-data prefix of every achievements button


def resolve_owner(session: Session, viewer_id: int, args: list[str]) -> int | None:
    """Whose achievements to show: `@user` from the args, else the viewer.
    None when the named player is unknown."""
    if not args:
        return viewer_id
    player = players.find_player_by_username(session, args[0].lstrip("@"))
    return None if player is None else player.telegram_user_id


def dm_link(context: ContextTypes.DEFAULT_TYPE, payload: str) -> str | None:
    """The deep link that opens the bot's DM with `/start <payload>` (None
    until app.py's _post_init has learned the bot's username)."""
    username = context.bot_data.get("bot_username")
    return f"https://t.me/{username}?start={payload}" if username else None


async def outsider_refusal(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, what: str
) -> str | None:
    """None for a member of the group; else the refusal to send, logged.
    Anyone can DM the bot, and these views show group members' names and
    activity, so they are for members only — the same gate as the shop."""
    if await is_group_member(context.bot, context.bot_data["group_chat_id"], user_id):
        return None
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
    logger.warning("{what} refused: not a group member", what=what)
    return i18n.t("dm_start.not_a_member", lang)
