"""/title — pick the achievement title shown next to your name (DM only)."""

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID, outsider_refusal
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.achievements import catalogue, names, titles

TITLE_PREFIX = "title:"
_NONE = "none"


async def title_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    refusal = await outsider_refusal(context, user.id, "/title")
    if refusal is not None:
        await message.reply_text(refusal)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        rows = [
            [
                InlineKeyboardButton(
                    names.title(catalogue.get(g.key), g.tier, g.period_key, lang),
                    callback_data=f"{TITLE_PREFIX}{g.id}",
                )
            ]
            for g in titles.eligible(session, user.id)
        ]
    logger.info("opened /title with {count} title(s)", count=len(rows))
    if not rows:
        await message.reply_text(i18n.t("title.none_earned", lang))
        return
    rows.append(
        [InlineKeyboardButton(i18n.t("title.clear", lang), callback_data=f"{TITLE_PREFIX}{_NONE}")]
    )
    await message.reply_text(i18n.t("title.pick", lang), reply_markup=InlineKeyboardMarkup(rows))


def _parse(data: str) -> tuple[bool, int | None]:
    """(valid, grant id) from a tap; the id is None for "no title"."""
    raw = data.removeprefix(TITLE_PREFIX)
    if raw == _NONE:
        return True, None
    if raw.isdecimal() and int(raw) <= MAX_ID:
        return True, int(raw)
    logger.warning("ignored a malformed /title tap {data!r}", data=data)
    return False, None


def _apply(context: ContextTypes.DEFAULT_TYPE, user_id: int, data: str) -> tuple[str, str | None]:
    """Store the tap's choice; (language, the title now shown) or None for a refusal."""
    valid, grant_id = _parse(data)
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        if not valid or not titles.choose(session, user_id, grant_id):
            return lang, None
        player = session.get(Player, user_id)
        return lang, (titles.text(player.title_key, lang) if player else None) or ""


async def title_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    await query.answer()
    lang, chosen = _apply(context, query.from_user.id, query.data)
    if chosen is None:
        await query.edit_message_text(i18n.t("title.stale", lang))
        return
    key = "title.set" if chosen else "title.cleared"
    await query.edit_message_text(i18n.t(key, lang, title=chosen))
