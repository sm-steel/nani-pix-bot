"""/grantitem @username <item_id> — DM only, group admins. Hands a season
cosmetic to a player outside the normal way of earning it (seasons spec §3)."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.admin_dm import admin_dm
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import ItemSource
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant


async def grant_item_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    gate = await admin_dm(update, context, "grantitem")
    if gate is None:
        return
    lang = gate.lang
    args = context.args or []
    if len(args) != 2:
        logger.info("sent /grantitem with bad args {args!r} — replied with usage", args=args)
        await gate.message.reply_text(i18n.t("grant_item.usage", lang))
        return
    username, item_id = args[0].lstrip("@"), args[1]
    resolved = registry.item(item_id)
    if resolved is None:
        logger.warning("tried /grantitem of unknown item {item_id!r}", item_id=item_id)
        await gate.message.reply_text(i18n.t("grant_item.unknown_item", lang, item_id=item_id))
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        player = players.find_player_by_username(session, username)
        if player is None:
            logger.warning(
                "tried /grantitem on unknown username {target_username!r}",
                target_username=username,
            )
            text = i18n.t("grant_item.unknown_player", lang, username=username)
        else:
            created = inventory.grant(
                session, player.telegram_user_id, ItemGrant(item_id, ItemSource.ADMIN)
            )
            key = "grant_item.granted" if created else "grant_item.already_owned"
            text = i18n.t(
                key,
                lang,
                item=resolved.item.name(lang),
                item_id=item_id,
                player=players.display_name(session, player.telegram_user_id),
            )
    await gate.message.reply_text(text)
