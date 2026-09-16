"""Shared body for DM-only, admin-gated on/off toggle commands
(`/setgamesenabled`, `/setautostart`, and any future one shaped like
them). Extracted because `/setautostart` mirroring `/setgamesenabled`
"exactly" produced two 40+-line near-identical blocks — a qlty
duplication finding — and the only real per-command difference is which
setting gets flipped and which i18n keys/log label describe it. Built as
a config object + factory (rather than a function with one parameter per
varying piece) so each command module ends up as a single config value,
not a second near-identical function body — which qlty's duplication
checker still flagged when the varying pieces were plain parameters."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings


@dataclass(frozen=True)
class AdminToggleConfig:
    """Everything that differs between one admin on/off toggle command
    and another: its name (for the log line), its i18n keys, and the
    setting it flips."""

    command_name: str
    usage_key: str
    enabled_key: str
    disabled_key: str
    setter: Callable[[Session, bool], None]


def make_admin_toggle_command(
    config: AdminToggleConfig,
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]:
    """Builds a `CommandHandler`-ready async callback: DM-only,
    admin-gated, parses `on|off` from `context.args`, and calls
    `config.setter(session, enabled)`."""

    async def toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        user = update.effective_user
        if not is_private_chat(update) or message is None or user is None:
            return

        session_factory = context.bot_data["session_factory"]
        group_chat_id = context.bot_data["group_chat_id"]
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
            if not await is_group_admin(context.bot, group_chat_id, user.id):
                logger.warning("Non-admin {} tried /{}", user.id, config.command_name)
                await message.reply_text(i18n.t("commands.admins_only", lang))
                return

        args = context.args or []
        if len(args) != 1 or args[0].lower() not in ("on", "off"):
            await message.reply_text(i18n.t(config.usage_key, lang))
            return
        enabled = args[0].lower() == "on"

        with session_scope(session_factory) as session:
            config.setter(session, enabled)

        await message.reply_text(
            i18n.t(config.enabled_key, lang) if enabled else i18n.t(config.disabled_key, lang)
        )

    return toggle_command
