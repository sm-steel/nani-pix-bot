"""Small helpers shared by more than one submodule of this package."""

import re
from typing import cast

import httpx
from loguru import logger
from telegram import InputMediaPhoto
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start.keyboards import method_selection_keyboard, preview_keyboard
from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models.enums import SetupStep
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.settings import stage_config

# "The provider is unreachable" in all the shapes it actually arrives in.
#
# httpx.HTTPError/RuntimeError are what services/search/ raises on a
# network failure, exhausted rate-limit retries, or a body that came back
# 200 but wasn't the JSON we asked for (a throttle page served as HTML,
# say) — see rest.py's get_json()/http_retry(), which is where that
# plumbing lives since the REST refactor deleted the per-provider
# _request() helpers this comment used to point at.
#
# TelegramError belongs with them because the gallery hands provider URLs
# to Telegram to fetch server-side (see screenshots.py's
# _show_gallery_page): a hotlink block, an over-10MB frame or a dead CDN
# path surfaces as a telegram.error.BadRequest on our side rather than as
# an httpx error of our own. Same cause, same thing to tell the starter,
# so the same exit — and until it was in here it escaped every handler in
# this package and left them on a SETUP row with a dead keyboard.
_SEARCH_SERVICE_ERRORS = (httpx.HTTPError, RuntimeError, TelegramError)

# Deliberately narrower than the tuple above, for the one call that is a
# plain httpx GET against a provider's CDN and nothing else:
# screenshot_gallery.py's download of the picked screenshot's bytes.
# Catching RuntimeError there would take any ordinary bug in that code
# path, swallow its traceback and report it to the starter as "the
# provider is down" — sending them round the source menu for a provider
# that is working fine.
_IMAGE_DOWNLOAD_ERRORS = (httpx.HTTPError,)

_SERVICE_DISPLAY_NAMES = {
    "anilist": "AniList",
    "shikimori": "Shikimori",
    "jikan": "Jikan",
    "tmdb": "TMDB",
}

# Used by both manual.py's second-message step and preview.py's
# "add a synonym" step.
_SYNONYM_SPLIT_RE = re.compile(r"[,\n]")

# TMDB is the only provider needing its own client (DNS-blocked direct
# from moscow, needs a proxy + Bearer-token auth — see app.py's
# build_application()); every other provider shares "search_client".
# Used by search.py's search/pick flow and both screenshots.py's/
# screenshot_gallery.py's gallery flow.
_TMDB_CLIENT_BOT_DATA_KEY = "tmdb_client"


def _client_for_source(context: ContextTypes.DEFAULT_TYPE, source: str) -> httpx.AsyncClient:
    """The httpx client to talk to `source` with. Annotated on both ends
    on purpose: `bot_data` is an untyped dict, so an unannotated return
    made this Unknown — and since every search, screenshot fetch and
    image download in the package funnels through here, that one Unknown
    was enough to stop `ty` checking a single call made on a client
    anywhere downstream. app.py puts a real AsyncClient under both keys
    (see build_application), so the cast states what is already true
    rather than papering over a doubt."""
    key = _TMDB_CLIENT_BOT_DATA_KEY if source == "tmdb" else "search_client"
    return cast(httpx.AsyncClient, context.bot_data[key])


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


async def _show_preview(context, session, game: Game, lang: str) -> None:
    """Send the starter a private preview of every pixelation stage for
    the staged title/synonyms — one Telegram album (blockiest to
    clearest, see MECHANICS.md) — so they see how the round will
    actually look before committing to it. `sendMediaGroup` has no
    `reply_markup` support, so the confirm/change-image/research/add-
    synonym buttons go on a short separate follow-up text message right
    after the album, not on the album itself. Nothing is posted to the
    group until "Confirm and start" is tapped. Always the game's own
    starter's DM — every caller looked this game up by starter_id in the
    first place, so game.starter_id is the right chat_id.

    Shared by every path that ends in "an image now exists for this
    game" — the traditional upload flow (intake.py, manual.py,
    preview.py's own add-synonym step), and the screenshot-picker flow
    (screenshot_gallery.py's pick), hence living here rather than in
    preview.py."""
    assert game.original_image is not None
    original_bytes = game.original_image
    config = stage_config.get_stage_config(session)
    title = game_service.display_title(game, lang)
    # Every stored title variant is already an accepted /guess — not just
    # the manually-typed synonyms — so show all of them here too, minus
    # whatever's already shown as the Title above. match_candidates() is
    # the same list record_guess() actually matches against, so this can
    # never drift from what's really accepted (see issue #50).
    other_answers = [
        candidate
        for candidate in dict.fromkeys(game_service.match_candidates(game))
        if candidate != title
    ]
    answers = ", ".join(other_answers) or "—"
    caption = i18n.t("dm_start.preview_caption", lang, title=title, answers=answers)
    captions = [caption, *([None] * (len(game_service.STAGE_ORDER) - 1))]
    media = [
        InputMediaPhoto(
            media=pixelate_service.pixelate(original_bytes, config[stage].target_width),
            caption=stage_caption,
        )
        for stage, stage_caption in zip(game_service.STAGE_ORDER, captions, strict=True)
    ]
    game.setup_step = SetupStep.CONFIRMING
    logger.debug("Game {}: showing {}-stage confirmation preview album", game.id, len(media))
    await context.bot.send_media_group(chat_id=game.starter_id, media=media)
    await context.bot.send_message(
        chat_id=game.starter_id,
        text=i18n.t("dm_start.preview_confirm_prompt", lang),
        reply_markup=preview_keyboard(lang),
    )
