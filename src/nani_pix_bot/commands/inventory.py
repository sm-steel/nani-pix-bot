"""/inventory — the caller's clue tokens and charms, in DM or the game topic."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import ConsumableKind, GameStatus
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.cosmetics import consumables, ofuda
from nani_pix_bot.services.game import state as game_service

_LINE_KEYS = {
    ConsumableKind.CLUE_TICKET: "inventory.line.clue_token",
    ConsumableKind.OFUDA: "inventory.line.ofuda",
    ConsumableKind.OMAMORI: "inventory.line.omamori",
}


def inventory_text(holdings: dict[ConsumableKind, int], *, ofuda_armed: bool, lang: str) -> str:
    """Header plus one line per held kind, in enum order."""
    lines = []
    for kind in ConsumableKind:
        count = holdings.get(kind, 0)
        if count <= 0:
            continue
        line = i18n.t(_LINE_KEYS[kind], lang, count=count)
        if kind is ConsumableKind.OFUDA and ofuda_armed:
            line += i18n.t("inventory.ofuda_armed", lang)
        lines.append(line)
    if not lines:
        return i18n.t("inventory.empty", lang)
    if holdings.get(ConsumableKind.OFUDA, 0) > 0 and not ofuda_armed:
        lines.append(i18n.t("inventory.ofuda_hint", lang))
    return "\n".join([i18n.t("inventory.header", lang), *lines])


async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return
    in_topic = is_game_topic(
        update,
        group_chat_id=context.bot_data["group_chat_id"],
        game_topic_id=context.bot_data["game_topic_id"],
    )
    if not (in_topic or is_private_chat(update)):
        return

    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        held = consumables.holdings(session, user.id)
        game = game_service.active_or_setup_game(session)
        armed = (
            game is not None
            and game.status == GameStatus.ACTIVE
            and ofuda.is_armed(session, game.id, user.id)
        )
    logger.info(
        "checked /inventory: {holdings}",
        holdings={kind.value: count for kind, count in held.items()},
    )
    await message.reply_text(inventory_text(held, ofuda_armed=armed, lang=lang))
