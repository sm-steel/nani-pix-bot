"""The /version command — reports the running build's version and, when
GitHub has a matching release, its release notes rendered inline. No
scope/membership gating: usable in both DM and the group game topic,
same posture as /help's non-redirect branch (see MECHANICS.md's
onboarding.help entry for that precedent)."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings, version


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)

    running_version = version.installed_version()
    search_client = context.bot_data["search_client"]
    notes = await version.fetch_release_notes(search_client, running_version)

    if notes is None:
        text = i18n.t("version.no_notes", lang, version=running_version)
    else:
        text = i18n.t(
            "version.reply",
            lang,
            version=running_version,
            notes=version.render_release_notes_html(notes),
        )

    logger.debug("/version requested, running version {}", running_version)
    await message.reply_text(text, parse_mode="HTML")
