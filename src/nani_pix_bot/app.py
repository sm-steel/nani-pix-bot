"""Application entry point: wires the python-telegram-bot Application,
registers command handlers, and re-arms any pending 2-day timeout
JobQueue jobs from the database on startup (JobQueue jobs don't survive
a process restart — see MECHANICS.md's "Timeout" section)."""

import httpx
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from nani_pix_bot import db
from nani_pix_bot.commands import correct, dm_start, guess, leaderboard, skip
from nani_pix_bot.commands.timeout import rearm_pending_timeouts
from nani_pix_bot.config import Config, load_config
from nani_pix_bot.logging_config import setup_logging


def build_application(config: Config) -> Application:
    builder = ApplicationBuilder().token(config.bot_token)
    if config.telegram_proxy_url:
        builder = builder.proxy(config.telegram_proxy_url).get_updates_proxy(
            config.telegram_proxy_url
        )
    application = builder.post_init(_post_init).build()

    engine = db.get_engine(config.database_url)
    application.bot_data["session_factory"] = db.make_session_factory(engine)
    application.bot_data["anilist_client"] = httpx.AsyncClient(timeout=30)
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
    application.add_handler(CallbackQueryHandler(dm_start.pick_callback_handler))
    application.add_handler(CommandHandler("guess", guess.guess_command))
    application.add_handler(CommandHandler("correct", correct.correct_command))
    application.add_handler(CommandHandler("skip", skip.skip_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard.leaderboard_command))

    return application


async def _post_init(application: Application) -> None:
    session_factory = application.bot_data["session_factory"]
    await rearm_pending_timeouts(application.job_queue, session_factory)


def main() -> None:
    config = load_config()
    setup_logging(config.log_level)
    application = build_application(config)
    application.run_polling()


if __name__ == "__main__":
    main()
