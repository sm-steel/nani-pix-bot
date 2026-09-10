"""The /language command — DM only, gated to group admins/owners. See
CLAUDE.md's i18n notes."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.commands.onboarding import refresh_command_menu
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings

SET_LANGUAGE_PREFIX = "set_language:"


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🇷🇺 Русский", callback_data=f"{SET_LANGUAGE_PREFIX}RU")],
            [InlineKeyboardButton("🇬🇧 English", callback_data=f"{SET_LANGUAGE_PREFIX}EN")],
        ]
    )


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        if not await is_group_admin(context.bot, group_chat_id, user.id):
            await message.reply_text(i18n.t("language.not_admin", lang))
            return

    await message.reply_text(i18n.t("language.prompt", lang), reply_markup=_keyboard())


async def language_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    user = query.from_user
    if user is None:
        return
    await query.answer()

    new_lang = query.data.removeprefix(SET_LANGUAGE_PREFIX)

    group_chat_id = context.bot_data["group_chat_id"]
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        if not await is_group_admin(context.bot, group_chat_id, user.id):
            return
        settings.set_language(session, new_lang)

    await refresh_command_menu(context.bot, group_chat_id=group_chat_id, lang=new_lang)
    await query.edit_message_text(i18n.t("language.set", new_lang))
