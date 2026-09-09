"""Private-chat photo intake + AniList search/pick flow — see
MECHANICS.md's "Starting a game" section."""

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.keyboards import (
    RETRY_CALLBACK_DATA,
    anilist_results_keyboard,
    parse_pick_callback_data,
)
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.commands.timeout import schedule_timeout
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import anilist
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import pixelate as pixelate_service

PENDING_GAME_ID_KEY = "pending_game_id"
SEARCH_RESULTS_KEY = "search_results"


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A DM photo starts game setup, if it's this player's turn."""
    message = update.message
    user_data = context.user_data
    if not is_private_chat(update) or message is None or not message.photo or user_data is None:
        return
    user = update.effective_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        game_service.get_or_create_player(session, user.id, username=user.username)
        if not game_service.can_start(session, user.id):
            await message.reply_text("It's not your turn to start a new game right now.")
            return
        file_id = message.photo[-1].file_id
        setup_game = game_service.create_setup_game(
            session, starter_id=user.id, original_file_id=file_id
        )
        game_id = setup_game.id

    user_data[PENDING_GAME_ID_KEY] = game_id
    await message.reply_text("Got it! What anime is this from? Type a search query.")


async def search_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A DM text message, once a game is pending, is an AniList search query."""
    message = update.message
    user_data = context.user_data
    if not is_private_chat(update) or message is None or message.text is None or user_data is None:
        return
    if PENDING_GAME_ID_KEY not in user_data:
        return

    client = context.bot_data["anilist_client"]
    results = await anilist.search(client, message.text)
    if not results:
        await message.reply_text("No AniList results for that — try a different search.")
        return

    user_data[SEARCH_RESULTS_KEY] = {result.anilist_id: result for result in results}
    await message.reply_text("Which one is it?", reply_markup=anilist_results_keyboard(results))


async def pick_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The starter tapped a result (or "none of these") from search_text_handler's
    keyboard. A valid pick pixelates the original at X10, posts it to the
    group's game topic, and activates the game."""
    query = update.callback_query
    user_data = context.user_data
    if query is None or query.data is None or user_data is None:
        return
    await query.answer()

    if query.data == RETRY_CALLBACK_DATA:
        await query.edit_message_text("Okay, type a new search query.")
        return

    anilist_id = parse_pick_callback_data(query.data)
    results = user_data.get(SEARCH_RESULTS_KEY, {})
    game_id = user_data.get(PENDING_GAME_ID_KEY)
    if anilist_id is None or game_id is None or anilist_id not in results:
        return
    result = results[anilist_id]

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        setup_game = session.get(Game, game_id)
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

    user_data.pop(PENDING_GAME_ID_KEY, None)
    user_data.pop(SEARCH_RESULTS_KEY, None)
    await query.edit_message_text("Posted! Good luck to everyone guessing.")
