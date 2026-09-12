"""Application entry point: wires the python-telegram-bot Application,
registers command handlers, and re-arms any pending 2-day timeout
JobQueue jobs from the database on startup (JobQueue jobs don't survive
a process restart — see MECHANICS.md's "Timeout" section)."""

import re

import httpx
from loguru import logger
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from nani_pix_bot import db
from nani_pix_bot.commands import (
    correct,
    dm_start,
    guess,
    language,
    leaderboard,
    onboarding,
    skip,
    stageconfig,
    stop,
    testpixels,  # TEMPORARY — see commands/testpixels.py
)
from nani_pix_bot.commands.helpers.bot_menu import refresh_command_menu
from nani_pix_bot.commands.helpers.keyboards import RETRY_CALLBACK_DATA
from nani_pix_bot.commands.language import SET_LANGUAGE_PREFIX
from nani_pix_bot.config import Config, load_config
from nani_pix_bot.jobs.timers import rearm_pending_timeouts
from nani_pix_bot.logging_config import setup_logging
from nani_pix_bot.services import settings


def build_application(config: Config) -> Application:
    builder = ApplicationBuilder().token(config.bot_token)
    if config.telegram_proxy_url:
        builder = builder.proxy(config.telegram_proxy_url).get_updates_proxy(
            config.telegram_proxy_url
        )
    application = builder.post_init(_post_init).build()

    engine = db.get_engine(config.database_url)
    application.bot_data["session_factory"] = db.make_session_factory(engine)
    # Shared by both anilist.py and shikimori.py — a plain HTTP client,
    # nothing service-specific about it (each module sends its own
    # headers per request).
    application.bot_data["search_client"] = httpx.AsyncClient(timeout=30)
    application.bot_data["group_chat_id"] = config.group_chat_id
    application.bot_data["game_topic_id"] = config.game_topic_id

    application.add_handler(
        MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, dm_start.photo_handler)
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
            dm_start.search_text_handler,
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            dm_start.pick_callback_handler,
            pattern=rf"^({re.escape(RETRY_CALLBACK_DATA)}|anilist_pick:|shikimori_pick:)",
        )
    )
    application.add_handler(
        CallbackQueryHandler(dm_start.method_pick_callback_handler, pattern=r"^method:")
    )
    application.add_handler(
        CallbackQueryHandler(dm_start.preview_callback_handler, pattern=r"^preview:")
    )
    application.add_handler(
        CallbackQueryHandler(
            language.language_callback_handler, pattern=rf"^{re.escape(SET_LANGUAGE_PREFIX)}"
        )
    )
    application.add_handler(CallbackQueryHandler(stop.stop_callback_handler, pattern=r"^stop:"))
    application.add_handler(CommandHandler("guess", guess.guess_command))
    application.add_handler(CommandHandler("correct", correct.correct_command))
    application.add_handler(CommandHandler("skip", skip.skip_command))
    application.add_handler(CommandHandler("stop", stop.stop_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard.leaderboard_command))
    application.add_handler(CommandHandler("language", language.language_command))
    application.add_handler(CommandHandler("start", onboarding.start_command))
    application.add_handler(CommandHandler("help", onboarding.help_command))
    application.add_handler(CommandHandler("testpixels", testpixels.testpixels_command))
    application.add_handler(CommandHandler("stageconfig", stageconfig.stageconfig_command))
    application.add_handler(CommandHandler("setstageconfig", stageconfig.setstageconfig_command))
    application.add_handler(CommandHandler("setstage", stageconfig.setstage_command))

    return application


async def _post_init(application: Application) -> None:
    session_factory = application.bot_data["session_factory"]
    await rearm_pending_timeouts(application.job_queue, session_factory)

    me = await application.bot.get_me()
    application.bot_data["bot_username"] = me.username
    logger.info("Logged in as @{}", me.username)

    with db.session_scope(session_factory) as session:
        lang = settings.get_language(session)
    await refresh_command_menu(
        application.bot, group_chat_id=application.bot_data["group_chat_id"], lang=lang
    )


def main() -> None:
    config = load_config()
    setup_logging(config.log_level)
    application = build_application(config)
    application.run_polling()


if __name__ == "__main__":
    main()
