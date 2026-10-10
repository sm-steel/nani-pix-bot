"""Manual title/synonym entry — for anime neither AniList nor Shikimori
knows about. Two DM text messages: the title, then at least one
synonym."""

from loguru import logger
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import (
    _SYNONYM_SPLIT_RE,
)
from nani_pix_bot.commands.dm_start.aliases import start_alias_search
from nani_pix_bot.commands.dm_start.screenshots import (
    send_screenshot_picker_prompt,
    stage_screenshot_picker,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n


async def _manual_title_step(message, context: ContextTypes.DEFAULT_TYPE, lang: str, user) -> None:
    """The first manual-entry text message: the anime's title."""
    title = message.text.strip()
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None:
            logger.warning("typed a manual title with no SETUP game left — ignoring")
            return
        if title:
            setup_game.title_english = title
            logger.info("entered manual title {title!r}", title=title, game_id=setup_game.id)
        else:
            logger.info("sent a blank manual title — asking again", game_id=setup_game.id)

    reply_key = "dm_start.ask_synonyms" if title else "dm_start.ask_manual_title"
    await message.reply_text(i18n.t(reply_key, lang))


async def _manual_synonyms_step(
    message, context: ContextTypes.DEFAULT_TYPE, lang: str, user
) -> None:
    """The second manual-entry text message: at least one synonym. On
    success, stages the entry and shows the confirmation preview — same
    as an AniList/Shikimori pick."""
    synonyms = [s.strip() for s in _SYNONYM_SPLIT_RE.split(message.text) if s.strip()]
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        title = setup_game.title_english if setup_game is not None else None
        if setup_game is None or title is None:
            logger.warning("typed manual synonyms with no SETUP game (or no title) left — ignoring")
            return
        if not synonyms:
            logger.info("sent no usable synonym — asking again", game_id=setup_game.id)
            await message.reply_text(i18n.t("dm_start.synonyms_required", lang))
            return
        game_service.stage_manual_entry(setup_game, title=title, synonyms=synonyms)
        logger.info(
            "entered synonyms {synonyms!r} for manual title {title!r}",
            synonyms=synonyms,
            title=title,
            game_id=setup_game.id,
        )
        game_id = setup_game.id
        has_image = setup_game.original_image is not None
        if has_image:
            # Traditional photo-first entry — image already in hand. The
            # preview follows the alias search (aliases.py).
            message_key = "dm_start.preview_sent"
        else:
            # Screenshot-less /newgame entry — no image in hand yet.
            # Manual entry has no external id of its own, but that
            # stopped meaning "upload or nothing" when cross-provider
            # resolution landed: game_service.screenshot_capable_providers offers all
            # three providers unconditionally, and tapping one silently
            # searches it by the title just staged (stage_manual_entry
            # puts it in title_english, which is what that search reads).
            # So a manual game gets the full source menu, with "Upload my
            # own instead" one button on it rather than the only route.
            picker_prompt = stage_screenshot_picker(setup_game)
            message_key = "dm_start.identification_staged"
    # Block closed and committed above — see _post_preview_album's/
    # send_screenshot_picker_prompt's docstrings for why the send has to
    # happen after.
    start_alias_search(context, game_id, then_preview_in=lang if has_image else None)
    if not has_image:
        await send_screenshot_picker_prompt(context, picker_prompt, lang)

    await message.reply_text(i18n.t(message_key, lang))
