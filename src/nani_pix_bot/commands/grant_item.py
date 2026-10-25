"""/grantitem [@username] <item_id> — DM only, group admins. Hands a season
cosmetic to a player outside the normal way of earning it (seasons spec §3).
Without the @username, a menu of every known player (grant_item_picker.py)."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import grant_item_apply, grant_item_picker
from nani_pix_bot.commands.helpers.admin_dm import admin_dm
from nani_pix_bot.db import session_scope
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n, players

# /grantitem @username <item id>
_WITH_USERNAME = 2


def _parse(args: list[str]) -> tuple[str | None, str] | None:
    """(username or None, item id) from `[@username] <item id>`; None for any
    other shape, including a lone @username with no item."""
    if len(args) == 1 and not args[0].startswith("@"):
        return None, args[0]
    if len(args) == _WITH_USERNAME:
        return args[0].lstrip("@"), args[1]
    return None


async def grant_item_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    gate = await admin_dm(update, context, "grantitem")
    if gate is None:
        return
    lang = gate.lang
    args = context.args or []
    parsed = _parse(args)
    if parsed is None:
        logger.warning("sent /grantitem with bad args {args!r} — replied with usage", args=args)
        await gate.message.reply_text(i18n.t("grant_item.usage", lang))
        return
    username, item_id = parsed
    resolved = registry.item(item_id)
    if resolved is None:
        logger.warning("tried /grantitem of unknown item {item_id!r}", item_id=item_id)
        await gate.message.reply_text(i18n.t("grant_item.unknown_item", lang, item_id=item_id))
        return
    keyboard = None
    with session_scope(context.bot_data["session_factory"]) as session:
        if username is None:
            text, keyboard = grant_item_picker.open_view(
                session, gate.user_id, resolved, context.bot.id
            )
        else:
            text = _grant_to(session, username, resolved, lang)
    await gate.message.reply_text(text, reply_markup=keyboard)


def _grant_to(session: Session, username: str, resolved: ResolvedItem, lang: str) -> str:
    player = players.find_player_by_username(session, username)
    if player is None:
        logger.warning(
            "tried /grantitem on unknown username {target_username!r}", target_username=username
        )
        return i18n.t("grant_item.unknown_player", lang, username=username)
    return grant_item_apply.give(session, player.telegram_user_id, resolved, lang)
