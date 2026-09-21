"""/linkmal and /unlinkmal — standalone, DM-only, self-service entry
points for MAL account linking (see
docs/superpowers/specs/2026-09-21-mal-account-linking-design.md).
Unlike /language (commands/language.py), there's no admin gate here —
a player links/unlinks only their own account. The method-picker's 6th
button (commands/dm_start/mal_browse.py, a later task) is a convenience
wrapper around the same underlying linking flow, not a replacement for
these commands."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers.mal_link_expiry import schedule_mal_link_expiry
from nani_pix_bot.services import i18n, mal_link, settings
from nani_pix_bot.services.search import mal_user


async def linkmal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_chat(update) or update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    mal_client_id = context.bot_data.get("mal_client_id")
    mal_redirect_uri = context.bot_data.get("mal_redirect_uri")
    session_factory = context.bot_data["session_factory"]

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        if not mal_client_id or not mal_redirect_uri:
            logger.warning(
                "Player {} ran /linkmal but MAL account linking isn't configured", user.id
            )
            await update.message.reply_text(i18n.t("mal_link.not_configured", lang))
            return

        state, code_verifier = mal_user.generate_state_and_verifier()
        mal_link.upsert_pending_link(session, user.id, state=state, code_verifier=code_verifier)
        authorize_url = mal_user.build_authorize_url(
            client_id=mal_client_id,
            redirect_uri=mal_redirect_uri,
            state=state,
            code_verifier=code_verifier,
        )

    schedule_mal_link_expiry(context.job_queue, user.id)
    logger.info("Player {} started a /linkmal attempt", user.id)
    await update.message.reply_text(i18n.t("mal_link.authorize_prompt", lang, url=authorize_url))


async def unlinkmal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_chat(update) or update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    session_factory = context.bot_data["session_factory"]

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        mal_link.delete_credentials(session, user.id)

    await update.message.reply_text(i18n.t("mal_link.unlinked", lang))
