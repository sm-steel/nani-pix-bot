"""Application entry point: wires the python-telegram-bot Application,
registers command handlers, and re-arms any pending 2-day timeout
JobQueue jobs from the database on startup (JobQueue jobs don't survive
a process restart — see MECHANICS.md's "Timeout" section)."""

import re

import httpx
from loguru import logger
from telegram import Update
from telegram.error import Conflict, NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from nani_pix_bot import db, heartbeat
from nani_pix_bot.commands import (
    dm_start,
    game_flow,
    gamesenabled,
    language,
    leaderboard,
    onboarding,
    setautostart,
    stageconfig,
    version,
)
from nani_pix_bot.commands.dm_start.keyboards import (
    SCREENSHOT_UPLOAD_CALLBACK_DATA,
    SEARCH_RETRY_CALLBACK_DATA,
)
from nani_pix_bot.commands.helpers import player_tracking
from nani_pix_bot.commands.helpers.bot_menu import refresh_command_menu
from nani_pix_bot.commands.language import SET_LANGUAGE_PREFIX
from nani_pix_bot.config import Config, load_config
from nani_pix_bot.jobs.timers import rearm_pending_timeouts
from nani_pix_bot.logging_config import setup_logging
from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services import settings

# python-telegram-bot's ApplicationBuilder defaults this to 1 (general
# Bot-API requests default to 256) — a single connection that fails
# mid-handshake instead of cleanly erroring can wedge it permanently,
# silently killing all future polling (see the 2026-09-13 incident,
# issue #51). This doesn't fix that underlying httpx/httpcore behavior,
# it just means one stuck connection is no longer enough to wedge
# everything — see heartbeat.py + docker-compose.yml's healthcheck for
# the actual detection/recovery.
GET_UPDATES_CONNECTION_POOL_SIZE = 4

# Everything else registers into PTB's default group (0). Named rather
# than literal so tests can assert "before the commands" instead of
# hard-coding -1 in two places.
_PLAYER_TRACKING_GROUP = -1

# Which identification-search pick prefixes route to
# pick_callback_handler. Built from Provider.pick_prefix rather than typed
# out as four literals: this pattern decides which handler even *sees* an
# update, so a fifth provider added to the enum and to keyboards.py but
# forgotten here would produce buttons whose taps silently reach nothing
# at all. See keyboards.py's `<provider>_pick:` prefixes (also read off
# Provider.pick_prefix), which this has to keep matching.
_PICK_PREFIX_PATTERN = "|".join(re.escape(provider.pick_prefix) for provider in Provider)


def build_application(config: Config) -> Application:
    builder = ApplicationBuilder().token(config.bot_token)
    builder = builder.get_updates_connection_pool_size(GET_UPDATES_CONNECTION_POOL_SIZE)
    if config.telegram_proxy_url:
        builder = builder.proxy(config.telegram_proxy_url).get_updates_proxy(
            config.telegram_proxy_url
        )
    application = builder.post_init(_post_init).post_shutdown(_post_shutdown).build()

    engine = db.get_engine(config.database_url)
    application.bot_data["session_factory"] = db.make_session_factory(engine)
    # Shared by anilist.py/shikimori.py — a plain HTTP client, nothing
    # service-specific about it (each module sends its own headers per
    # request). Both are reachable directly, no proxy needed.
    application.bot_data["search_client"] = httpx.AsyncClient(timeout=30)
    if not config.tmdb_read_access_token:
        # Optional by design (config.py) — but without this, a fresh
        # clone offers a TMDB identification method whose every call
        # 401s and reads to the starter as "the service is down", with
        # nothing anywhere saying why. Never log the token itself: this
        # repo is public and so are the deploy logs' audience.
        logger.warning(
            "TMDB_READ_ACCESS_TOKEN is not set — TMDB search and screenshots will "
            "fail as if the service were down until it is configured (see .env.example)"
        )
    # tmdb.py needs its own client: TMDB may be blocked/unreachable on
    # some hosts (reachable via the same optional proxy Telegram already
    # uses, if configured — see ARCHITECTURE.md's connectivity section)
    # and needs a Bearer-token Authorization header on every request, set
    # here as a client default so tmdb.py itself never has to touch the
    # secret.
    application.bot_data["tmdb_client"] = httpx.AsyncClient(
        timeout=30,
        proxy=config.telegram_proxy_url,
        headers=(
            {"Authorization": f"Bearer {config.tmdb_read_access_token}"}
            if config.tmdb_read_access_token
            else {}
        ),
    )
    # tenrai.py gets its own client too, same as tmdb.py's above — may
    # need the same optional proxy, but no auth header: Tenrai's public
    # tier needs none (see services/search/tenrai.py's module docstring).
    application.bot_data["tenrai_client"] = httpx.AsyncClient(
        timeout=30, proxy=config.telegram_proxy_url
    )
    application.bot_data["group_chat_id"] = config.group_chat_id
    application.bot_data["game_topic_id"] = config.game_topic_id

    # The only handler outside the default group. -1 runs it before every
    # command handler below (PTB walks groups in order and carries on to
    # the next one unless a callback raises ApplicationHandlerStop, which
    # this one never does), so a user is recorded from the very update
    # that first mentions them — including the /correct that needs them.
    application.add_handler(
        TypeHandler(Update, player_tracking.remember_user), group=_PLAYER_TRACKING_GROUP
    )

    application.add_handler(
        MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, dm_start.photo_handler)
    )
    application.add_handler(CommandHandler("newgame", dm_start.newgame_command))
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
            dm_start.search_text_handler,
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            dm_start.pick_callback_handler,
            pattern=rf"^({re.escape(SEARCH_RETRY_CALLBACK_DATA)}|{_PICK_PREFIX_PATTERN})",
        )
    )
    application.add_handler(
        CallbackQueryHandler(dm_start.method_pick_callback_handler, pattern=r"^method:")
    )
    application.add_handler(
        CallbackQueryHandler(
            dm_start.screenshot_upload_instead_callback_handler,
            pattern=rf"^{re.escape(SCREENSHOT_UPLOAD_CALLBACK_DATA)}$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            dm_start.screenshot_source_callback_handler, pattern=r"^screenshot_source:"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            dm_start.screenshot_gallery_callback_handler,
            pattern=r"^(screenshot_pick:|screenshot_more:)",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            dm_start.screenshot_search_again_callback_handler,
            pattern=r"^screenshot_search_again:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            dm_start.screenshot_search_pick_callback_handler,
            pattern=r"^screenshot_search_pick:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(dm_start.preview_callback_handler, pattern=r"^preview:")
    )
    application.add_handler(
        CallbackQueryHandler(
            language.language_callback_handler, pattern=rf"^{re.escape(SET_LANGUAGE_PREFIX)}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(game_flow.stop_callback_handler, pattern=r"^stop:")
    )
    application.add_handler(CommandHandler("guess", game_flow.guess_command))
    application.add_handler(CommandHandler("correct", game_flow.correct_command))
    application.add_handler(CommandHandler("skip", game_flow.skip_command))
    application.add_handler(CommandHandler("stop", game_flow.stop_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard.leaderboard_command))
    application.add_handler(CommandHandler("language", language.language_command))
    application.add_handler(CommandHandler("start", onboarding.start_command))
    application.add_handler(CommandHandler("help", onboarding.help_command))
    application.add_handler(CommandHandler("stageconfig", stageconfig.stageconfig_command))
    application.add_handler(CommandHandler("setstageconfig", stageconfig.setstageconfig_command))
    application.add_handler(CommandHandler("setstage", stageconfig.setstage_command))
    application.add_handler(CommandHandler("setgamesenabled", gamesenabled.setgamesenabled_command))
    application.add_handler(CommandHandler("setautostart", setautostart.setautostart_command))
    application.add_handler(CommandHandler("version", version.version_command))
    application.add_error_handler(_error_handler)

    return application


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Without this, PTB just logs "No error handlers are registered,
    logging exception" — which is how the 2026-09-13 getUpdates
    connection-pool wedge (issue #51) produced hours of DEBUG-only
    "Failed run number N of -1. Retrying." spam with nothing at
    WARNING/ERROR level to catch it. Network hiccups (including a 409
    Conflict, which self-heals — see that incident's postmortem) are
    expected occasionally and log at WARNING; anything else reaching
    here is an actual bug and gets a full traceback at ERROR."""
    error = context.error
    if isinstance(error, NetworkError | Conflict):
        logger.warning("Network error talking to Telegram: {}", error)
        return
    logger.opt(exception=error).error("Unhandled exception while processing update: {}", update)


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


async def _post_shutdown(application: Application) -> None:
    """The three `httpx.AsyncClient`s built in build_application() are
    ours, not PTB's, so nothing else closes them. In production they're
    process-lifetime objects and this is just tidiness on the way out;
    in tests, where an Application is built per case, it's what stops
    three clients leaking every time."""
    for key in ("search_client", "tmdb_client", "tenrai_client"):
        client = application.bot_data.get(key)
        if client is not None:
            await client.aclose()
    logger.debug("Closed the search HTTP clients")


def main() -> None:
    config = load_config()
    setup_logging(config.log_level)
    application = build_application(config)
    heartbeat.install(application)
    application.run_polling()


if __name__ == "__main__":
    main()
