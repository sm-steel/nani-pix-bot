"""Small helpers shared by more than one submodule of this package."""

import re

import httpx
from loguru import logger

from nani_pix_bot.commands.dm_start.keyboards import method_selection_keyboard
from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings

# Raised by anilist.py/shikimori.py on network failure or exhausted
# rate-limit retries — see their _request() helpers.
_SEARCH_SERVICE_ERRORS = (httpx.HTTPError, RuntimeError)

_SERVICE_DISPLAY_NAMES = {
    "anilist": "AniList",
    "shikimori": "Shikimori",
    "jikan": "Jikan",
    "tmdb": "TMDB",
}

# Used by both manual.py's second-message step and preview.py's
# "add a synonym" step.
_SYNONYM_SPLIT_RE = re.compile(r"[,\n]")


def _prefer_shikimori(lang: str) -> bool:
    return lang.upper() == "RU"


def _method_prompt_key(*, prefer_shikimori: bool) -> str:
    return (
        "dm_start.pick_method_prompt_shikimori_preferred"
        if prefer_shikimori
        else "dm_start.pick_method_prompt"
    )


async def _reply_service_down(send, lang: str, source: str) -> None:
    """Shared failure path for both the search step and the pick step:
    tell the starter the chosen service looks unreachable and hand them
    back the method-selection keyboard rather than leaving them stuck
    with a dead-end SETUP game (see issue #11's orphaned-row incident)."""
    prefer_shikimori = _prefer_shikimori(lang)
    await send(
        i18n.t("dm_start.search_failed", lang, service=_SERVICE_DISPLAY_NAMES[source]),
        reply_markup=method_selection_keyboard(prefer_shikimori=prefer_shikimori, lang=lang),
    )


async def _start_new_game(
    message, context, session_factory, user, image_bytes: bytes | None = None
) -> None:
    """Creates a new SETUP game and shows the method-selection keyboard —
    shared by both entry points into the setup flow: `intake.py`'s
    `photo_handler` (real image bytes already in hand) and `newgame.py`'s
    `/newgame` command (no image yet — filled in later, either by
    picking a screenshot or by falling back to an upload)."""
    group_chat_id = context.bot_data["group_chat_id"]
    if not await is_group_member(context.bot, group_chat_id, user.id):
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        logger.warning("Non-member {} tried to start a game via DM", user.id)
        await message.reply_text(i18n.t("dm_start.not_a_member", lang))
        return

    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        if not settings.get_games_enabled(session):
            logger.warning("{} tried to start a game while games are disabled", user.id)
            await message.reply_text(i18n.t("dm_start.games_disabled", lang))
            return
        players.get_or_create_player(session, user.id, username=user.username)
        if not game_service.can_start(session, user.id):
            logger.warning("{} tried to start a game out of turn", user.id)
            await message.reply_text(i18n.t("dm_start.not_your_turn", lang))
            return
        new_game = game_service.create_setup_game(
            session, starter_id=user.id, original_image=image_bytes
        )
        # They're clearly not missing their turn if they've already
        # started it — the setup-abandon timer takes over from here.
        game_service.clear_turn_timers(session)

    timeout_module.cancel_turn_timers(context.job_queue)
    timeout_module.schedule_setup_abandon(context.job_queue, new_game)
    await context.bot.send_message(
        chat_id=group_chat_id,
        message_thread_id=context.bot_data["game_topic_id"],
        text=i18n.t("dm_start.setup_started_group_notice", lang, starter=user.full_name),
    )

    prefer_shikimori = _prefer_shikimori(lang)
    await message.reply_text(
        i18n.t(_method_prompt_key(prefer_shikimori=prefer_shikimori), lang),
        reply_markup=method_selection_keyboard(prefer_shikimori=prefer_shikimori, lang=lang),
    )
