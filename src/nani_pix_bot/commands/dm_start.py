"""Private-chat photo intake + AniList search/pick flow — see
MECHANICS.md's "Starting a game" section.

Deliberately keeps no state in PTB's in-memory `user_data`: which game a
DM is setting up is derived from the DB (`get_setup_game_for_starter`),
and which AniList result a tapped button means is re-fetched by id
(`anilist.get_by_id`) rather than cached — both survive a bot restart
mid-setup, which in-memory `user_data` doesn't (see issue #11).
"""

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.keyboards import (
    RETRY_CALLBACK_DATA,
    anilist_results_keyboard,
    parse_pick_callback_data,
)
from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.commands.timeout import schedule_timeout
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.services import anilist
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import pixelate as pixelate_service


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A DM photo starts game setup, if it's this player's turn."""
    message = update.message
    if not is_private_chat(update) or message is None or not message.photo:
        return
    user = update.effective_user
    if user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    if not await is_group_member(context.bot, group_chat_id, user.id):
        await message.reply_text("You need to be a member of the group to start a game.")
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        game_service.get_or_create_player(session, user.id, username=user.username)
        if not game_service.can_start(session, user.id):
            await message.reply_text("It's not your turn to start a new game right now.")
            return
        file_id = message.photo[-1].file_id
        game_service.create_setup_game(session, starter_id=user.id, original_file_id=file_id)

    await message.reply_text("Got it! What anime is this from? Type a search query.")


async def search_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A DM text message, once a game is pending, is an AniList search query."""
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or message.text is None or user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        has_pending_game = game_service.get_setup_game_for_starter(session, user.id) is not None
    if not has_pending_game:
        return

    client = context.bot_data["anilist_client"]
    results = await anilist.search(client, message.text)
    if not results:
        await message.reply_text("No AniList results for that — try a different search.")
        return

    await message.reply_text("Which one is it?", reply_markup=anilist_results_keyboard(results))


async def pick_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The starter tapped a result (or "none of these") from search_text_handler's
    keyboard. A valid pick pixelates the original at X10, posts it to the
    group's game topic, and activates the game."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    if query.data == RETRY_CALLBACK_DATA:
        await query.edit_message_text("Okay, type a new search query.")
        return

    anilist_id = parse_pick_callback_data(query.data)
    user = query.from_user
    if anilist_id is None or user is None:
        return

    client = context.bot_data["anilist_client"]
    result = await anilist.get_by_id(client, anilist_id)
    if result is None:
        await query.edit_message_text(
            "Couldn't find that on AniList anymore — try searching again."
        )
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        setup_game = game_service.get_setup_game_for_starter(session, user.id)
        if setup_game is None or setup_game.original_file_id is None:
            return
        telegram_file = await context.bot.get_file(setup_game.original_file_id)
        original_bytes = bytes(await telegram_file.download_as_bytearray())
        pixelated = pixelate_service.pixelate(original_bytes, PixelStage.X10)
        await context.bot.send_photo(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            photo=pixelated,
        )
        game_service.activate_game(session, setup_game, result)
        schedule_timeout(context.job_queue, setup_game)

    await query.edit_message_text("Posted! Good luck to everyone guessing.")
