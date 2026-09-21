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

from nani_pix_bot.commands.helpers.mal_config import mal_configured
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers.mal_link_expiry import schedule_mal_link_expiry
from nani_pix_bot.services import i18n, mal_link, settings
from nani_pix_bot.services.search import mal_user


async def linkmal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private_chat(update) or update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    session_factory = context.bot_data["session_factory"]

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        # All four MAL settings or none — the same gate the method
        # picker's 6th button uses (commands/helpers/mal_config.py).
        # Checking only client id + redirect uri, as this used to, let a
        # player finish a real OAuth consent and paste the code back on a
        # bot with no MAL_TOKEN_ENCRYPTION_KEY, where storing the tokens
        # then blew up — with their single-use code already spent.
        if not mal_configured(context.bot_data):
            logger.warning(
                "Player {} ran /linkmal but MAL account linking isn't configured", user.id
            )
            await update.message.reply_text(i18n.t("mal_link.not_configured", lang))
            return

        state, code_verifier = mal_user.generate_state_and_verifier()
        mal_link.upsert_pending_link(session, user.id, state=state, code_verifier=code_verifier)
        authorize_url = mal_user.build_authorize_url(
            client_id=context.bot_data["mal_client_id"],
            redirect_uri=context.bot_data["mal_redirect_uri"],
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
        # An in-flight /linkmal is part of "my MAL link", so /unlinkmal
        # has to clear it too. Left behind, it outranks everything in
        # search_text_handler: the player's next plain DM — a search
        # query, a manual title, a synonym — would be eaten as a pasted
        # authorization code and answered with a rejection.
        mal_link.delete_pending_link(session, user.id)

    logger.info("Player {} ran /unlinkmal", user.id)
    await update.message.reply_text(i18n.t("mal_link.unlinked", lang))
